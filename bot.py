import os
import time
import math
import hmac
import hashlib
import json
import logging
from flask import Flask, request, jsonify
import requests

# Логи
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# Конфиг из окружения
API_KEY         = os.getenv("BYBIT_API_KEY")
API_SECRET      = os.getenv("BYBIT_API_SECRET")
BASE_URL        = os.getenv("BYBIT_BASE_URL", "https://api-testnet.bybit.com")
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

app = Flask(__name__)

# Подпись
def sign(path: str, query: str = "", body: str = "") -> tuple[str,str]:
    timestamp = str(int(time.time() * 1000))
    origin = timestamp + API_KEY + path + query + body
    signature = hmac.new(API_SECRET.encode(), origin.encode(), hashlib.sha256).hexdigest()
    return timestamp, signature

# Получить параметры symbol: minOrderQty, qtyStep
# Исправлено: вызываем только ?category=linear, затем фильтруем по символу
def get_symbol_info(symbol: str) -> tuple[float, float]:
    path = "/v5/market/symbols"
    query = "?category=linear"
    ts, sig = sign(path, query)
    headers = {
        'X-BAPI-API-KEY': API_KEY,
        'X-BAPI-TIMESTAMP': ts,
        'X-BAPI-SIGN': sig,
        'Content-Type': 'application/json'
    }
    res = requests.get(BASE_URL + path + query, headers=headers)
    if res.status_code != 200:
        logger.error(f"Symbols request failed {res.status_code}: {res.text}")
        raise RuntimeError("Could not fetch symbol info")
    data = res.json()
    # Ищем нужный символ в списке
    for info in data.get('result', {}).get('list', []):
        if info.get('name') == symbol or info.get('symbol') == symbol:
            return float(info['minOrderQty']), float(info['qtyStep'])
    logger.error(f"Symbol {symbol} not found in symbols list")
    raise RuntimeError(f"Symbol {symbol} info missing")

# Получить цену
def get_price(symbol: str) -> float:
    path = "/v5/market/tickers"
    query = f"?symbol={symbol}&category=linear"
    ts, sig = sign(path, query)
    headers = {
        'X-BAPI-API-KEY': API_KEY,
        'X-BAPI-TIMESTAMP': ts,
        'X-BAPI-SIGN': sig,
    }
    res = requests.get(BASE_URL + path + query, headers=headers)
    price = float(res.json()['result']['list'][0]['lastPrice'])
    return price

# Отправить телеграмму
def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': text}
    requests.post(url, data=payload)

# Разместить ордер с учётом minOrderQty и шага
def place_order(symbol: str, side: str, qty: float, reduce_only: bool=False):
    price = get_price(symbol)
    min_qty, step = get_symbol_info(symbol)
    raw_min = max(5/price, min_qty)
    min_qty_calc = math.ceil(raw_min/step) * step
    qty_adj = math.ceil(qty/step) * step
    final_qty = max(qty_adj, min_qty_calc)

    body = {
        "category": "linear",
        "symbol": symbol,
        "side": side,
        "orderType": "Market",
        "qty": str(final_qty),
        "timeInForce": "ImmediateOrCancel",
        "reduceOnly": reduce_only
    }
    path = "/v5/order/create"
    bstr = json.dumps(body)
    ts, sig = sign(path, "", bstr)
    headers = {
        'X-BAPI-API-KEY': API_KEY,
        'X-BAPI-TIMESTAMP': ts,
        'X-BAPI-SIGN': sig,
        'Content-Type': 'application/json'
    }
    resp = requests.post(BASE_URL + path, headers=headers, data=bstr)
    try:
        result = resp.json()
    except ValueError:
        logger.error(f"Order response not JSON: {resp.status_code} {resp.text}")
        result = {'retMsg': 'Invalid response'}
    logger.info(f"Order {side} {symbol} qty {final_qty} => {result}")
    send_telegram(f"{side} {symbol} qty={final_qty} → {result}")

# Вебхук от TradingView
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    logger.debug(f"Webhook payload {data}")
    symbol = data['symbol']
    side   = data['side'].capitalize()
    qty    = float(data.get('qty', 1))
    reduce = 'exit' in data['side']

    try:
        place_order(symbol, side, qty, reduce)
        return jsonify({'status':'ok'})
    except Exception as e:
        logger.exception("Error handling webhook")
        send_telegram(f"Error: {e}")
        return jsonify({'status':'error', 'message': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 10000)))
