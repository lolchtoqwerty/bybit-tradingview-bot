import os
import time
import math
import hmac
import hashlib
import json
from flask import Flask, request, jsonify
import requests

# Load config from env
API_KEY = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")
BASE_URL = os.getenv("BYBIT_BASE_URL", "https://api-testnet.bybit.com")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

app = Flask(__name__)

# Helper: send message to Telegram
def send_telegram(message: str):
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
        try:
            requests.post(url, json=payload)
        except Exception as e:
            print(f"Telegram error: {e}")

# Helper: sign for Bybit v5
def sign_v5(ts: str, recv_window: str, body: str) -> str:
    to_sign = ts + API_KEY + recv_window + body
    return hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()

# Get USDT wallet balance
def get_balance() -> float:
    path = "/v5/account/wallet-balance"
    ts = str(int(time.time() * 1000))
    recv_window = "5000"
    body = ""
    sign = sign_v5(ts, recv_window, body)
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv_window,
        "X-BAPI-SIGN": sign,
    }
    url = BASE_URL + path + "?category=linear"
    r = requests.get(url, headers=headers)
    try:
        result = r.json().get('result', {}).get('list', [])
        for entry in result:
            if entry.get('coin') == 'USDT':
                return float(entry.get('equity', 0))
        print(f"USDT not found in wallet balance, response: {r.text}")
        return None
    except Exception as e:
        print(f"Error fetching balance: {e}, response: {r.text}")
        return None

# Get current mark price for symbol
def get_mark_price(symbol: str) -> float:
    path = "/v5/market/tickers"
    ts = str(int(time.time() * 1000))
    recv_window = "5000"
    sign = sign_v5(ts, recv_window, "")
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv_window,
        "X-BAPI-SIGN": sign,
    }
    url = f"{BASE_URL}{path}?category=linear&symbol={symbol}"
    r = requests.get(url, headers=headers)
    resp = r.json()
    lst = resp.get('result', {}).get('list', [])
    if not lst:
        print(f"Price fetch error, response: {resp}")
        return None
    return float(lst[0].get('lastPrice', 0))

# Get current position size
def get_position_size(symbol: str) -> float:
    path = "/v5/position/list"
    ts = str(int(time.time() * 1000))
    recv_window = "5000"
    sign = sign_v5(ts, recv_window, "")
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv_window,
        "X-BAPI-SIGN": sign,
    }
    url = f"{BASE_URL}{path}?category=linear&symbol={symbol}"
    r = requests.get(url, headers=headers).json()
    lst = r.get('result', {}).get('list', [])
    if not lst:
        return 0.0
    return float(lst[0].get('size', 0))

# Place a market order
def place_order(symbol: str, side: str, qty: float, reduce_only: bool=False) -> dict:
    price = get_mark_price(symbol)
    if price:
        min_qty = math.ceil(5 / price)
        if qty < min_qty:
            print(f"Qty {qty} < min {min_qty}, adjusting")
            qty = min_qty
    path = "/v5/order/create"
    ts = str(int(time.time() * 1000))
    recv_window = "5000"
    body = json.dumps({
        "category": "linear",
        "symbol": symbol,
        "side": side.capitalize(),
        "orderType": "Market",
        "qty": str(qty),
        "timeInForce": "ImmediateOrCancel",
        "reduceOnly": reduce_only,
    })
    sign = sign_v5(ts, recv_window, body)
    headers = {
        "Content-Type": "application/json",
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv_window,
        "X-BAPI-SIGN": sign,
    }
    url = BASE_URL + path
    r = requests.post(url, headers=headers, data=body)
    try:
        res = r.json()
    except Exception:
        res = {"ret_code": -1, "ret_msg": r.text}
    send_telegram(f"{side} {symbol} qty={qty} → {res}")
    return res

# Webhook endpoint
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json(force=True)
    symbol = data.get('symbol')
    side = data.get('side')
    qty = float(data.get('qty', 1))
    pos_size = get_position_size(symbol)

    if side == 'buy':
        place_order(symbol, 'Buy', qty)
    elif side == 'sell':
        place_order(symbol, 'Sell', qty)
    elif side == 'exit long':
        if pos_size > 0:
            place_order(symbol, 'Sell', qty, reduce_only=True)
        bal = get_balance()
        send_telegram(f"Баланс после лонга: {bal} USDT")
    elif side == 'exit short':
        if pos_size < 0:
            place_order(symbol, 'Buy', qty, reduce_only=True)
        bal = get_balance()
        send_telegram(f"Баланс после шорта: {bal} USDT")
    else:
        return jsonify(error="unknown side"), 400
    return jsonify(status="ok")

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
