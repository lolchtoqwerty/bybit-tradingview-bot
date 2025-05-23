import os
import time
import math
import hmac
import hashlib
import json
import logging
from flask import Flask, request, jsonify
import requests

# Настройка логирования
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# Конфигурация из переменных окружения
API_KEY          = os.getenv("BYBIT_API_KEY")
API_SECRET       = os.getenv("BYBIT_API_SECRET")
BASE_URL         = os.getenv("BYBIT_BASE_URL", "https://api-testnet.bybit.com")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

app = Flask(__name__)

# Функция подписи запросов
def sign(path: str, query: str = "", body: str = "") -> tuple[str, str]:
    timestamp = str(int(time.time() * 1000))
    origin = timestamp + API_KEY + path + query + body
    signature = hmac.new(
        API_SECRET.encode(), origin.encode(), hashlib.sha256
    ).hexdigest()
    return timestamp, signature

# Получаем фильтры для количества ордера (minQty и шаг)
def get_symbol_info(symbol: str) -> tuple[float, float]:
    path = "/v5/market/instruments-info"
    query = f"?category=linear&symbol={symbol}"
    ts, sig = sign(path, query)
    headers = {
        'X-BAPI-API-KEY':   API_KEY,
        'X-BAPI-TIMESTAMP': ts,
        'X-BAPI-SIGN':      sig,
    }
    url = BASE_URL + path + query
    logger.debug(f"[SymbolInfo] GET {url}")
    res = requests.get(url, headers=headers)
    if res.status_code != 200:
        logger.error(f"Symbols request failed {res.status_code}: {res.text}")
        raise RuntimeError("Could not fetch symbol info")
    data = res.json().get('result', {}).get('list', [])
    for info in data:
        if info.get('symbol') == symbol:
            lot = info.get('lotSizeFilter', {})
            min_qty = float(lot.get('minOrderQty', 0))
            step    = float(lot.get('qtyStep', 0))
            logger.info(f"Symbol {symbol}: minQty={min_qty}, step={step}")
            return min_qty, step
    logger.error(f"Symbol {symbol} not found in instruments-info response")
    raise RuntimeError(f"Symbol {symbol} info missing")

# Получаем текущую цену символа
def get_price(symbol: str) -> float:
    path = "/v5/market/tickers"
    query = f"?symbol={symbol}&category=linear"
    ts, sig = sign(path, query)
    headers = {
        'X-BAPI-API-KEY':   API_KEY,
        'X-BAPI-TIMESTAMP': ts,
        'X-BAPI-SIGN':      sig,
    }
    url = BASE_URL + path + query
    logger.debug(f"[Price] GET {url}")
    res = requests.get(url, headers=headers)
    price = float(res.json()['result']['list'][0]['lastPrice'])
    logger.info(f"Price {symbol}: {price}")
    return price

# Отправка сообщения в Telegram
def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': text}
    logger.debug(f"[Telegram] POST {url} payload={payload}")
    requests.post(url, data=payload)

# Размещаем рыночный ордер
def place_order(symbol: str, side: str, qty: float, reduce_only: bool=False):
    try:
        price = get_price(symbol)
        min_qty, step = get_symbol_info(symbol)
    except Exception as e:
        logger.exception("Error fetching symbol info or price")
        send_telegram(f"Error retrieving market data: {e}")
        return

    # Рассчитываем минимальное количество для $5 базового объема
    raw_min = max(5/price, min_qty)
    min_calc = math.ceil(raw_min/step) * step
    qty_adj  = math.ceil(qty/step) * step
    final_qty = max(qty_adj, min_calc)

    body = {
        "category":    "linear",
        "symbol":      symbol,
        "side":        side,
        "orderType":   "Market",
        "qty":         str(final_qty),
        "timeInForce": "ImmediateOrCancel",
        "reduceOnly":  reduce_only
    }
    # Используем компактный сериализатор без пробелов и в порядке ключей для корректной подписи
    payload_str = json.dumps(body, separators=(",","":"), sort_keys=True)
    path = "/v5/order/create"
    ts, sig = sign(path, "", payload_str)
    headers = {
        'X-BAPI-API-KEY':   API_KEY,
        'X-BAPI-TIMESTAMP': ts,
        'X-BAPI-SIGN':      sig,
        'Content-Type':     'application/json'
    }
    url = BASE_URL + path
    logger.debug(f"[Order] POST {url} body={payload_str}")
    res = requests.post(url, headers=headers, data=payload_str)
    try:
        result = res.json()
    except ValueError:
        logger.error(f"Order response not JSON: {res.status_code} {res.text}")
        result = {'retMsg': 'Invalid response'}

    logger.info(f"Order {side} {symbol} qty={final_qty} => {result}")
    send_telegram(f"{side} {symbol} qty={final_qty} → {result}")

# HTTP webhook endpoint для TradingView
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json(force=True)
    logger.debug(f"[Webhook] payload {data}")
    symbol = data.get('symbol')
    raw_side = data.get('side', '').lower()
    side = raw_side.capitalize()
    qty    = float(data.get('qty', 1))
    reduce = 'exit' in raw_side

    try:
        place_order(symbol, side, qty, reduce)
        return jsonify({'status':'ok'})
    except Exception as e:
        logger.exception("Error handling webhook")
        send_telegram(f"Error handling webhook: {e}")
        return jsonify({'status':'error','message':str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 10000)))
