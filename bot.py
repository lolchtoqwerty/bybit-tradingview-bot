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

logger.debug(f"Config - BASE_URL: {BASE_URL}, TELEGRAM_CHAT_ID: {TELEGRAM_CHAT_ID is not None}")

app = Flask(__name__)

# Helper: send message to Telegram
def send_telegram(message: str):
    logger.info(f"[Telegram] Sending message: {message}")
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        logger.warning("Telegram token or chat_id not set, skipping Telegram notification.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        resp = requests.post(url, json=payload)
        logger.info(f"[Telegram] Response status: {resp.status_code}, body: {resp.text}")
    except Exception as e:
        logger.error(f"[Telegram] Error sending message: {e}")

# Helper: sign for Bybit v5
def sign_v5(ts: str, recv_window: str, body: str) -> str:
    to_sign = ts + (API_KEY or "") + recv_window + body
    signature = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    logger.debug(f"[Auth] Signature: {signature}")
    return signature

# Get USDT wallet balance
def get_balance() -> float:
    path = "/v5/account/wallet-balance"
    ts = str(int(time.time() * 1000))
    recv_window = "5000"
    # Query string for GET
    query = "?category=linear"
    # Sign with path and query
    sign = sign_v5(ts, recv_window, path + query)
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv_window,
        "X-BAPI-SIGN": sign,
    }
    url = BASE_URL + path + query
    logger.debug(f"[Balance] Requesting balance: GET {url}")
    r = requests.get(url, headers=headers)
    logger.debug(f"[Balance] HTTP {r.status_code}: {r.text}")
    try:
        data_list = r.json().get('result', {}).get('list', [])
        for entry in data_list:
            if entry.get('coin') == 'USDT':
                balance = float(entry.get('equity', 0))
                logger.info(f"[Balance] USDT equity: {balance}")
                return balance
        logger.warning("[Balance] USDT entry not found in response")
    except Exception as e:
        logger.error(f"[Balance] Error parsing response JSON: {e}")
    return None

# Get current mark price for symbol
def get_mark_price(symbol: str) -> float:
    path = "/v5/market/tickers"
    ts = str(int(time.time() * 1000))
    recv_window = "5000"
    # Query string for GET
    query = f"?category=linear&symbol={symbol}"
    # Sign with path and query
    sign = sign_v5(ts, recv_window, path + query)
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv_window,
        "X-BAPI-SIGN": sign,
    }
    url = BASE_URL + path + query
    logger.debug(f"[Price] Requesting price: GET {url}")
    r = requests.get(url, headers=headers)
    logger.debug(f"[Price] HTTP {r.status_code}: {r.text}")
    try:
        items = r.json().get('result', {}).get('list', [])
        if not items:
            logger.warning("[Price] No price data returned")
            return None
        price = float(items[0].get('lastPrice', 0))
        logger.info(f"[Price] {symbol} lastPrice: {price}")
        return price
    except Exception as e:
        logger.error(f"[Price] Error parsing JSON: {e}")
        return None

# Get current position size
def get_position_size(symbol: str) -> float:
    path = "/v5/position/list"
    ts = str(int(time.time() * 1000))
    recv_window = "5000"
    query = f"?category=linear&symbol={symbol}"
    # Sign with path and query
    sign = sign_v5(ts, recv_window, path + query)
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv_window,
        "X-BAPI-SIGN": sign,
    }
    url = BASE_URL + path + query
    logger.debug(f"[Position] Requesting position: GET {url}")
    r = requests.get(url, headers=headers)
    logger.debug(f"[Position] HTTP {r.status_code}: {r.text}")
    try:
        result = r.json().get('result', {}).get('list', [])
        if not result:
            return 0.0
        size = float(result[0].get('size', 0))
        logger.info(f"[Position] {symbol} size: {size}")
        return size
    except Exception as e:
        logger.error(f"[Position] Error parsing JSON: {e}")
        return 0.0

# Place a market order
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
    logger.debug(f"[Price] Requesting price: GET {url}")
    r = requests.get(url, headers=headers)
    logger.debug(f"[Price] HTTP {r.status_code}: {r.text}")
    try:
        items = r.json().get('result', {}).get('list', [])
        if not items:
            logger.warning("[Price] No price data returned")
            return None
        price = float(items[0].get('lastPrice', 0))
        logger.info(f"[Price] {symbol} lastPrice: {price}")
        return price
    except Exception as e:
        logger.error(f"[Price] Error parsing JSON: {e}")
        return None

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
    logger.debug(f"[Position] Requesting position: GET {url}")
    r = requests.get(url, headers=headers)
    logger.debug(f"[Position] HTTP {r.status_code}: {r.text}")
    try:
        result = r.json().get('result', {}).get('list', [])
        if not result:
            return 0.0
        size = float(result[0].get('size', 0))
        logger.info(f"[Position] {symbol} size: {size}")
        return size
    except Exception as e:
        logger.error(f"[Position] Error parsing JSON: {e}")
        return 0.0

# Place a market order
def place_order(symbol: str, side: str, qty: float, reduce_only: bool=False) -> dict:
    price = get_mark_price(symbol)
    if price:
        min_qty = math.ceil(5 / price)
        if qty < min_qty:
            logger.warning(f"[Order] qty {qty} less than min {min_qty}, adjusting to min")
            qty = min_qty
    path = "/v5/order/create"
    ts = str(int(time.time() * 1000))
    recv_window = "5000"
    payload = {
        "category": "linear",
        "symbol": symbol,
        "side": side.capitalize(),
        "orderType": "Market",
        "qty": str(qty),
        "timeInForce": "ImmediateOrCancel",
        "reduceOnly": reduce_only,
    }
    body = json.dumps(payload)
    logger.debug(f"[Order] Creating order: POST {path} body={body}")
    sign = sign_v5(ts, recv_window, body)
    headers = {
        "Content-Type": "application/json",
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv_window,
        "X-BAPI-SIGN": sign,
    }
    url = BASE_URL + path
    response = requests.post(url, headers=headers, data=body)
    logger.info(f"[Order] HTTP {response.status_code}: {response.text}")
    try:
        res = response.json()
    except Exception:
        res = {"ret_code": -1, "ret_msg": response.text}
    send_telegram(f"{side} {symbol} qty={qty} → {res}")
    return res

# Webhook endpoint
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json(force=True)
    logger.debug(f"[Webhook] Received payload: {data}")
    symbol = data.get('symbol')
    side = data.get('side')
    qty = float(data.get('qty', 1))
    logger.info(f"[Webhook] Action: {side} {symbol} qty={qty}")
    pos_size = get_position_size(symbol)
    logger.debug(f"[Webhook] Current position size: {pos_size}")

    if side == 'buy':
        place_order(symbol, 'Buy', qty)
    elif side == 'sell':
        place_order(symbol, 'Sell', qty)
    elif side == 'exit long':
        if pos_size > 0:
            place_order(symbol, 'Sell', qty, reduce_only=True)
        else:
            logger.info("[Webhook] No long position to exit")
        balance = get_balance()
        send_telegram(f"Баланс после лонга: {balance} USDT")
    elif side == 'exit short':
        if pos_size < 0:
            place_order(symbol, 'Buy', qty, reduce_only=True)
        else:
            logger.info("[Webhook] No short position to exit")
        balance = get_balance()
        send_telegram(f"Баланс после шорта: {balance} USDT")
    else:
        logger.error(f"[Webhook] Unknown side: {side}")
        return jsonify(error="unknown side"), 400
    return jsonify(status="ok")

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    logger.info(f"Starting app on port {port}")
    app.run(host='0.0.0.0', port=port)
