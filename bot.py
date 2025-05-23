# bot.py - Bybit TradingView Webhook Bot
import os
import time
import json
import hmac
import hashlib
import logging
import requests
from flask import Flask, request
from math import ceil

# ——— Configuration ———
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")
BASE_URL = os.getenv("BYBIT_BASE_URL", "https://api-testnet.bybit.com")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")

# ——— Logging Setup ———
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] [%(funcName)s] %(message)s"
)
logger = logging.getLogger(__name__)

# ——— Signature Helper ———
def sign_request(path: str, payload_str: str = "", query: str = ""):
    """
    Create timestamp and HMAC SHA256 signature for Bybit API v5.
    Body-only signature due to testnet path signing discrepancy.
    """
    ts = str(int(time.time() * 1000))
    if payload_str:
        to_sign = ts + BYBIT_API_KEY + payload_str
    elif query:
        to_sign = ts + BYBIT_API_KEY + query
    else:
        to_sign = ts + BYBIT_API_KEY
    logger.debug(f"Signature string: {to_sign}")
    signature = hmac.new(
        BYBIT_API_SECRET.encode(),
        to_sign.encode(),
        hashlib.sha256
    ).hexdigest()
    logger.debug(f"Generated signature: {signature}")
    return ts, signature

# ——— HTTP Helpers ———
def http_get(path: str, params: dict = None):
    url = f"{BASE_URL}/{path}"
    query = ''
    if params:
        query = '&'.join(f"{k}={v}" for k, v in sorted(params.items()))
    ts, sign = sign_request(path, query=query)
    headers = {
        "X-BAPI-API-KEY": BYBIT_API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-SIGN": sign
    }
    logger.debug(f"HTTP GET {url}?{query}")
    resp = requests.get(url, headers=headers, params=params)
    logger.debug(f"Response [{resp.status_code}]: {resp.text}")
    return resp


def http_post(path: str, body: dict):
    url = f"{BASE_URL}/{path}"
    payload_str = json.dumps(body, separators=(",", ":"), sort_keys=True)
    ts, sign = sign_request(path, payload_str=payload_str)
    headers = {
        "Content-Type": "application/json",
        "X-BAPI-API-KEY": BYBIT_API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-SIGN": sign
    }
    logger.debug(f"HTTP POST {url} payload={payload_str}")
    resp = requests.post(url, headers=headers, data=payload_str)
    logger.debug(f"Response [{resp.status_code}]: {resp.text}")
    return resp

# ——— Bybit Utilities ———
def get_symbol_info(symbol: str):
    """
    Returns minimal quantity, step size, and minimum notional value for a given symbol.
    """
    logger.info(f"Fetching symbol info for {symbol}")
    path = "v5/market/instruments-info"
    params = {"category": "linear", "symbol": symbol}
    resp = http_get(path, params)
    data = resp.json()
    info = data["result"]["list"][0]
    filters = info.get("lotSizeFilter", {})
    min_qty = float(filters.get("minOrderQty", 0))
    step = float(filters.get("qtyStep", 1))
    min_notional = float(filters.get("minNotionalValue", 0))
    logger.info(f"Symbol {symbol}: minQty={min_qty}, step={step}, minNotional={min_notional}")
    return min_qty, step, min_notional


def get_ticker_price(symbol: str):
    """
    Fetches the current lastPrice for a symbol.
    """
    path = "v5/market/tickers"
    params = {"category": "linear", "symbol": symbol}
    resp = http_get(path, params)
    data = resp.json()
    price = float(data["result"]["list"][0]["lastPrice"])
    return price


def set_leverage(symbol: str, long_leverage: int = 3, short_leverage: int = 1):
    logger.info(f"Setting leverage for {symbol}: long={long_leverage}, short={short_leverage}")
    path = "v5/position/set-leverage"
    body = {
        "category": "linear",
        "symbol": symbol,
        "buyLeverage": long_leverage,
        "sellLeverage": short_leverage
    }
    resp = http_post(path, body)
    data = resp.json() if resp.text else {}
    if data.get("retCode") != 0:
        logger.warning(f"Leverage set failed: {data.get('retMsg')}")
    return data


def place_order(symbol: str, side: str, qty: float, reduce_only: bool = False):
    logger.info(f"Placing order: symbol={symbol}, side={side}, qty={qty}, reduce_only={reduce_only}")
    if not reduce_only:
        set_leverage(symbol)

    try:
        min_qty, step, min_notional = get_symbol_info(symbol)
    except Exception as e:
        logger.error(f"get_symbol_info error: {e}")
        return {"retCode": -1, "retMsg": str(e)}

    price = get_ticker_price(symbol)

    # Calculate adjusted qty to satisfy step size and notional requirements
    qty_step = step * ceil(qty / step)
    qty_notional = step * ceil(min_notional / (price * step))
    adjusted = max(min_qty, qty_step, qty_notional)
    logger.debug(f"Adjusted qty from {qty} to {adjusted}")

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
    result = resp.json() if resp.text else {"retCode": -1, "retMsg": resp.text}
    logger.info(f"Order response: {result}")
    return result


def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Telegram token or chat ID not set, skipping message")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    logger.debug(f"Sending Telegram: {text}")
    res = requests.post(url, json=payload)
    if not res.ok:
        logger.error(f"Telegram send failed: {res.text}")

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
