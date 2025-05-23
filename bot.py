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
API_KEY      = os.getenv("BYBIT_API_KEY")
API_SECRET   = os.getenv("BYBIT_API_SECRET")
BASE_URL     = os.getenv("BYBIT_BASE_URL", "https://api-testnet.bybit.com")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

app = Flask(__name__)

# Подпись
def sign(path: str, query: str = "", body: str = "") -> str:
    timestamp = str(int(time.time() * 1000))
    origin = timestamp + API_KEY + path + query + body
    signature = hmac.new(API_SECRET.encode(), origin.encode(), hashlib.sha256).hexdigest()
    return timestamp, signature

# Получить параметры symbol: minOrderQty, qtyStep
def get_symbol_info(symbol: str) -> tuple[float, float]:
    path = "/v5/market/symbols"
    query = f"?symbol={symbol}&category=linear"
    ts, sig = sign(path, query)
    headers = {
        'X-BAPI-API-KEY': API_KEY,
        'X-BAPI-TIMESTAMP': ts,
        'X-BAPI-SIGN': sig,
        'Content-Type': 'application/json'
    }
    res = requests.get(BASE_URL + path + query, headers=headers)
    data = res.json()
    info = data['result']['list'][0]
    return float(info['minOrderQty']), float(info['qtyStep'])

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
    result = resp.json()
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

    place_order(symbol, side, qty, reduce)
    return jsonify({'status':'ok'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 10000)))
