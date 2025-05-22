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
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# Load config from env
API_KEY = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")
BASE_URL = os.getenv("BYBIT_BASE_URL", "https://api-testnet.bybit.com")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

logger.debug(f"Config - BASE_URL: {BASE_URL}, TELEGRAM set: {bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)}")

app = Flask(__name__)

# Helper: send Telegram message
def send_telegram(message: str):
    logger.info(f"[Telegram] Sending: {message}")
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        logger.warning("Telegram config missing, skip sending")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    resp = requests.post(url, json=payload)
    logger.info(f"[Telegram] Response {resp.status_code}: {resp.text}")

# Auth signature
def sign_v5(timestamp: str, recv_window: str, req_path: str, body: str = "") -> str:
    msg = timestamp + (API_KEY or "") + recv_window + req_path + body
    sig = hmac.new(API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    logger.debug(f"[Auth] String to sign: {msg}")
    logger.debug(f"[Auth] Signature: {sig}")
    return sig

# Generic GET request
def bybit_get(path: str, params: dict) -> dict:
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    query = '&'.join(f"{k}={v}" for k, v in params.items())
    req_path = f"{path}?{query}"
    signature = sign_v5(timestamp, recv_window, req_path)
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
        "X-BAPI-SIGN": signature,
    }
    url = BASE_URL + req_path
    logger.debug(f"[GET] {url}")
    resp = requests.get(url, headers=headers)
    logger.debug(f"[GET] status {resp.status_code}: {resp.text}")
    try:
        return resp.json()
    except Exception:
        return {"ret_msg": resp.text}

# Generic POST request
def bybit_post(path: str, body: dict) -> dict:
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    req_path = path
    payload = json.dumps(body)
    signature = sign_v5(timestamp, recv_window, req_path, payload)
    headers = {
        "Content-Type": "application/json",
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
        "X-BAPI-SIGN": signature,
    }
    url = BASE_URL + path
    logger.debug(f"[POST] {url} body {payload}")
    resp = requests.post(url, headers=headers, data=payload)
    logger.debug(f"[POST] status {resp.status_code}: {resp.text}")
    try:
        return resp.json()
    except Exception:
        return {"ret_msg": resp.text}

# Business methods

def get_balance() -> float:
    data = bybit_get("/v5/account/wallet-balance", {"category": "linear"})
    for entry in data.get('result', {}).get('list', []):
        if entry.get('coin') == 'USDT':
            balance = float(entry.get('equity', 0))
            logger.info(f"[Balance] USDT equity {balance}")
            return balance
    logger.warning("[Balance] USDT not in wallet")
    return None

def get_mark_price(symbol: str) -> float:
    data = bybit_get("/v5/market/tickers", {"category": "linear", "symbol": symbol})
    items = data.get('result', {}).get('list', [])
    if not items:
        logger.warning("[Price] no data")
        return None
    price = float(items[0].get('lastPrice', 0))
    logger.info(f"[Price] {symbol} lastPrice {price}")
    return price

def get_position_size(symbol: str) -> float:
    data = bybit_get("/v5/position/list", {"category": "linear", "symbol": symbol})
    items = data.get('result', {}).get('list', [])
    if not items:
        return 0.0
    size = float(items[0].get('size', 0))
    logger.info(f"[Position] {symbol} size {size}")
    return size

def place_order(symbol: str, side: str, qty: float, reduce_only: bool=False) -> dict:
    price = get_mark_price(symbol)
    if price:
        min_qty = math.ceil(5 / price)
        if qty < min_qty:
            logger.warning(f"[Order] adjust qty {qty}->{min_qty}")
            qty = min_qty
    body = {
        "category": "linear",
        "symbol": symbol,
        "side": side.capitalize(),
        "orderType": "Market",
        "qty": str(qty),
        "timeInForce": "ImmediateOrCancel",
        "reduceOnly": reduce_only,
    }
    result = bybit_post("/v5/order/create", body)
    logger.info(f"[Order] {side} {symbol} qty {qty} => {result}")
    send_telegram(f"{side} {symbol} qty={qty} → {result}")
    return result

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json(force=True)
    logger.debug(f"[Webhook] payload {data}")
    symbol = data.get('symbol')
    side = data.get('side')
    qty = float(data.get('qty', 1))
    pos = get_position_size(symbol)
    logger.debug(f"[Webhook] current pos {pos}")
    if side == 'buy':
        place_order(symbol, 'Buy', qty)
    elif side == 'sell':
        place_order(symbol, 'Sell', qty)
    elif side == 'exit long':
        if pos > 0:
            place_order(symbol, 'Sell', qty, reduce_only=True)
        bal = get_balance()
        send_telegram(f"Баланс после лонга: {bal} USDT")
    elif side == 'exit short':
        if pos < 0:
            place_order(symbol, 'Buy', qty, reduce_only=True)
        bal = get_balance()
        send_telegram(f"Баланс после шорта: {bal} USDT")
    else:
        return jsonify(error="unknown side"), 400
    return jsonify(status="ok")

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    logger.info(f"Starting on port {port}")
    app.run(host='0.0.0.0', port=port)
