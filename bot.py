# bot.py - Bybit TradingView Webhook Bot
import os
import time
import json
import hmac
import hashlib
import logging
import requests
from flask import Flask, request

# ——— Configuration ———
API_KEY = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")
BASE_URL = "https://api-testnet.bybit.com"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ——— Logging Setup ———
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# ——— Helper Functions ———

def sign_request(path: str, body: dict = None, query: str = ""):
    """
    Create timestamp and HMAC SHA256 signature for Bybit API v5.
    Path should NOT include leading slash, e.g. "v5/order/create".
    If body is provided, JSON-dump with no spaces and sorted keys.
    """
    ts = str(int(time.time() * 1000))
    if body is not None:
        payload = json.dumps(body, separators=(",", ":"), sort_keys=True)
        to_sign = ts + API_KEY + path + payload
    else:
        to_sign = ts + API_KEY + path + query
    signature = hmac.new(
        API_SECRET.encode(),
        to_sign.encode(),
        hashlib.sha256
    ).hexdigest()
    return ts, signature


def http_get(path: str, params: dict = None):
    """
    Perform GET to BASE_URL/path, path without leading slash.
    """
    url = f"{BASE_URL}/{path}"
    query = ""
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items())
    ts, sign = sign_request(path, body=None, query=query)
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-SIGN": sign
    }
    logger.debug(f"[GET] {url}?{query} headers={headers}")
    return requests.get(url, headers=headers, params=params)


def http_post(path: str, body: dict):
    """
    Perform POST to BASE_URL/path, path without leading slash.
    """
    url = f"{BASE_URL}/{path}"
    ts, sign = sign_request(path, body=body)
    headers = {
        "Content-Type": "application/json",
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-SIGN": sign
    }
    logger.debug(f"[POST] {url} body={body} headers={headers}")
    return requests.post(url, headers=headers, json=body)


def get_symbol_info(symbol: str):
    """
    Fetch min order qty and step size for a given symbol.
    """
    path = "v5/market/instruments-info"
    params = {"category": "linear", "symbol": symbol}
    resp = http_get(path, params)
    data = resp.json()
    info = data["result"]["list"][0]
    min_qty = float(info["lotSizeFilter"]["minOrderQty"])
    step = float(info["lotSizeFilter"]["qtyStep"])
    logger.info(f"Symbol {symbol}: minQty={min_qty}, step={step}")
    return min_qty, step


def place_order(symbol: str, side: str, qty: float, reduce_only: bool = False):
    """
    Place a market order, adjusting quantity to the symbol's step.
    """
    try:
        min_qty, step = get_symbol_info(symbol)
    except Exception as e:
        logger.error(f"Symbol info error: {e}")
        return {"retCode": -1, "retMsg": "Symbol info error"}

    adjusted = max(min_qty, step * round(qty / step))
    body = {
        "category": "linear",
        "symbol": symbol,
        "side": side,
        "orderType": "Market",
        "qty": str(adjusted),
        "timeInForce": "ImmediateOrCancel",
        "reduceOnly": reduce_only
    }
    resp = http_post("v5/order/create", body)
    result = resp.json()
    logger.info(f"Order {side} {symbol} qty={adjusted} -> {result}")
    return result


def send_telegram(text: str):
    """
    Send a message to Telegram chat.
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Telegram token or chat ID not set")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    requests.post(url, json=payload)

# ——— Flask App ———
app = Flask(__name__)


@app.route("/webhook", methods=["POST"])
def webhook():
    payload = request.get_json(force=True)
    logger.debug(f"[Webhook] payload {payload}")
    symbol = payload.get("symbol")
    action = payload.get("side", "").lower()
    qty = float(payload.get("qty", 0))
    reduce_flag = action.startswith("exit")
    side = "Buy" if action in ["buy", "long"] else "Sell"

    result = place_order(symbol, side, qty, reduce_only=reduce_flag)
    msg = f"{side} {symbol} qty={qty} → {result}"
    send_telegram(msg)
    return {"status": "ok"}


if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
