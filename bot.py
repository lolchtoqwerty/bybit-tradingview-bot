# bot.py - Bybit TradingView Webhook Bot
import os
import time
import json
import hmac
import hashlib
import logging
import requests
from flask import Flask, request
from math import floor

# ——— Configuration ———
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")
BASE_URL = os.getenv("BYBIT_BASE_URL", "https://api-testnet.bybit.com")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")

LONG_LEVERAGE = 3  # кредитное плечо для лонга
SHORT_LEVERAGE = 1  # кредитное плечо для шорта

# ——— Logging Setup ———
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] [%(funcName)s] %(message)s"
)
logger = logging.getLogger(__name__)

# ——— Signature Helper ———
def sign_request(path: str, payload_str: str = "", query: str = ""):
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
    # Build query string in insertion order to match requests serialization
    query = ''
    if params:
        query = '&'.join(f"{k}={v}" for k, v in params.items())
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
def get_wallet_balance(coin: str = "USDT", account_type: str = "UNIFIED"):
    logger.info(f"Fetching wallet balance for {coin}, accountType={account_type}")
    path = "v5/account/wallet-balance"
    params = {"coin": coin, "accountType": account_type}
    resp = http_get(path, params)
    data = resp.json()
    if data.get("retCode") != 0:
        logger.error(f"Wallet balance API error: {data.get('retMsg')}")
        return 0.0
    items = data.get("result", {}).get("list", [])
    if not items:
        logger.error("Wallet balance API returned empty list")
        return 0.0
    bal_info = items[0]
    balance = float(bal_info.get("availableBalance", bal_info.get("equity", 0)))
    logger.info(f"Wallet {coin} availableBalance: {balance}")
    return balance


def get_symbol_info(symbol: str):
    logger.info(f"Fetching symbol info for {symbol}")
    path = "v5/market/instruments-info"
    params = {"category": "linear", "symbol": symbol}
    resp = http_get(path, params)
    data = resp.json()
    info = data.get("result", {}).get("list", [])[0]
    filters = info.get("lotSizeFilter", {})
    min_contract = float(filters.get("minOrderQty", 0))
    step = float(filters.get("qtyStep", 1))
    min_notional = float(filters.get("minNotionalValue", 0))
    logger.info(f"Symbol {symbol}: minContract={min_contract}, step={step}, minNotional={min_notional}")
    return min_contract, step, min_notional


def get_ticker_price(symbol: str):
    path = "v5/market/tickers"
    params = {"category": "linear", "symbol": symbol}
    resp = http_get(path, params)
    data = resp.json()
    price = float(data.get("result", {}).get("list", [])[0].get("lastPrice", 0))
    return price


def set_leverage(symbol: str, long_leverage: int = LONG_LEVERAGE, short_leverage: int = SHORT_LEVERAGE):
    logger.info(f"Setting leverage for {symbol}: long={long_leverage}, short={short_leverage}")
    path = "v5/position/set-leverage"
    body = {"category": "linear", "symbol": symbol, "buyLeverage": long_leverage, "sellLeverage": short_leverage}
    resp = http_post(path, body)
    try:
        data = resp.json()
    except ValueError:
        logger.error(f"Leverage API no JSON: {resp.text}")
        return {"retCode": -1, "retMsg": "No JSON from leverage API"}
    if data.get("retCode") != 0:
        logger.warning(f"Leverage set failed: {data.get('retMsg')}")
    return data


def place_order(symbol: str, side: str, qty: float = 0, reduce_only: bool = False):
    logger.info(f"Placing order: symbol={symbol}, side={side}, qty_param={qty}, reduce_only={reduce_only}")
    if side == "Buy" and not reduce_only:
        set_leverage(symbol, LONG_LEVERAGE, SHORT_LEVERAGE)
        balance = get_wallet_balance("USDT")
        notional = balance * LONG_LEVERAGE
        logger.debug(f"Notional USDT for BUY: {balance} * {LONG_LEVERAGE} = {notional}")
    else:
        notional = None
        contracts = qty

    min_contract, step, min_notional = get_symbol_info(symbol)
    price = get_ticker_price(symbol)

    if side == "Buy":
        if notional < min_notional:
            msg = f"Calculated notional {notional} USDT below minNotional {min_notional}"
            logger.error(msg)
            return {"retCode": -1, "retMsg": msg}
        contracts = step * floor(notional / (price * step))
        if contracts < min_contract:
            contracts = min_contract
        logger.debug(f"Calculated BUY contracts {contracts} for notional {notional} at {price}")

    body = {"category": "linear", "symbol": symbol, "side": side,
            "orderType": "Market", "qty": str(contracts),
            "timeInForce": "ImmediateOrCancel", "reduceOnly": reduce_only}
    resp = http_post("v5/order/create", body)
    try:
        result = resp.json()
    except ValueError:
        logger.error(f"Order API no JSON: {resp.text}")
        return {"retCode": -1, "retMsg": resp.text}
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
