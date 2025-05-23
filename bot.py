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
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")
BASE_URL = os.getenv("BYBIT_BASE_URL", "https://api-testnet.bybit.com")
# Support two var names for Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")

# ——— Logging Setup ———
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# ——— Signature Helper ———
def sign_request(path: str, body: dict = None, query: str = ""):
    """
    Create timestamp and HMAC SHA256 signature for Bybit API v5.
    Path should NOT include leading slash, e.g. "v5/order/create".
    body: payload for POST; query: raw query string without leading '?'.
    """
    ts = str(int(time.time() * 1000))
    request_path = f"/{path}"
    if body is not None:
        payload_str = json.dumps(body, separators=(",", ":"), sort_keys=True)
        to_sign = ts + BYBIT_API_KEY + request_path + payload_str
    else:
        query_str = f"?{query}" if query else ""
        to_sign = ts + BYBIT_API_KEY + request_path + query_str
    signature = hmac.new(
        BYBIT_API_SECRET.encode(),
        to_sign.encode(),
        hashlib.sha256
    ).hexdigest()
    return ts, signature

# ——— HTTP Helpers ———
def http_get(path: str, params: dict = None):
    url = f"{BASE_URL}/{path}"
    query = ''
    if params:
        # sort params so signature matches
        query = '&'.join(f"{k}={v}" for k, v in sorted(params.items()))
    ts, sign = sign_request(path, query=query)
    headers = {
        "X-BAPI-API-KEY": BYBIT_API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-SIGN": sign
    }
    logger.debug(f"[GET] {url}?{query} headers={headers}")
    return requests.get(url, headers=headers, params=params)


def http_post(path: str, body: dict):
    url = f"{BASE_URL}/{path}"
    ts, sign = sign_request(path, body=body)
    headers = {
        "Content-Type": "application/json",
        "X-BAPI-API-KEY": BYBIT_API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-SIGN": sign
    }
    logger.debug(f"[POST] {url} body={body} headers={headers}")
    return requests.post(url, headers=headers, json=body)

# ——— Bybit Utilities ———
def get_symbol_info(symbol: str):
    path = "v5/market/instruments-info"
    params = {"category": "linear", "symbol": symbol}
    resp = http_get(path, params)
    data = resp.json()
    if data.get("retCode") != 0 or not data.get("result", {}).get("list"):
        raise RuntimeError(f"Instrument info failed: {data.get('retMsg')}")
    info = data["result"]["list"][0]
    filters = info.get("lotSizeFilter", {})
    min_qty = float(filters.get("minOrderQty", 0))
    step = float(filters.get("qtyStep", 1))
    logger.info(f"Symbol {symbol}: minQty={min_qty}, step={step}")
    return min_qty, step


def set_leverage(symbol: str, long_leverage: int = 3, short_leverage: int = 1):
    path = "v5/position/leverage/save"
    body = {
        "category": "linear",
        "symbol": symbol,
        "buyLeverage": long_leverage,
        "sellLeverage": short_leverage
    }
    resp = http_post(path, body)
    try:
        data = resp.json()
    except ValueError:
        logger.error(f"Set leverage no JSON response: {resp.status_code} {resp.text}")
        return {"retCode": -1, "retMsg": "No JSON from leverage API"}
    if data.get("retCode") != 0:
        logger.warning(f"Set leverage failed: {data.get('retMsg')}")
    else:
        logger.info(f"Leverage set for {symbol}: long={long_leverage}, short={short_leverage}")
    return data


def place_order(symbol: str, side: str, qty: float, reduce_only: bool = False):
    # for new positions, apply customized leverage
    if not reduce_only:
        set_leverage(symbol)
    try:
        min_qty, step = get_symbol_info(symbol)
    except Exception as e:
        logger.error(f"Symbol info error: {e}")
        return {"retCode": -1, "retMsg": str(e)}

    adjusted_qty = max(min_qty, step * round(qty / step))
    body = {
        "category": "linear",
        "symbol": symbol,
        "side": side,
        "orderType": "Market",
        "qty": str(adjusted_qty),
        "timeInForce": "ImmediateOrCancel",
        "reduceOnly": reduce_only
    }
    resp = http_post("v5/order/create", body)
    try:
        result = resp.json()
    except ValueError:
        logger.error(f"Order response not JSON: {resp.status_code} {resp.text}")
        return {"retCode": -1, "retMsg": "No JSON from order API"}
    logger.info(f"Order {side} {symbol} qty={adjusted_qty} -> {result}")
    return result


def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Telegram token or chat ID not set, skipping message")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
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
