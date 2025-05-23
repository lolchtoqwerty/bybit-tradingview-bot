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
    query = ''
    if params:
        # preserve insertion order
        query = '&'.join(f"{k}={v}" for k, v in params.items())
    ts, sign = sign_request(path, query=query)
    headers = {"X-BAPI-API-KEY": BYBIT_API_KEY, "X-BAPI-TIMESTAMP": ts, "X-BAPI-SIGN": sign}
    logger.debug(f"HTTP GET {url}?{query}")
    resp = requests.get(url, headers=headers, params=params)
    logger.debug(f"Response [{resp.status_code}]: {resp.text}")
    return resp


def http_post(path: str, body: dict):
    url = f"{BASE_URL}/{path}"
    payload_str = json.dumps(body, separators=(",", ":"), sort_keys=True)
    ts, sign = sign_request(path, payload_str=payload_str)
    headers = {"Content-Type": "application/json", "X-BAPI-API-KEY": BYBIT_API_KEY,
               "X-BAPI-TIMESTAMP": ts, "X-BAPI-SIGN": sign}
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
    balance = float(bal_info.get("totalAvailableBalance", 0))
    logger.info(f"Wallet {coin} balance: {balance}")
    return balance


def get_symbol_info(symbol: str):
    logger.info(f"Fetching symbol info for {symbol}")
    path = "v5/market/instruments-info"
    params = {"category": "linear", "symbol": symbol}
    resp = http_get(path, params)
    data = resp.json()
    info = data.get("result", {}).get("list", [])[0]
    f = info.get("lotSizeFilter", {})
    return float(f.get("minOrderQty", 0)), float(f.get("qtyStep", 1)), float(f.get("minNotionalValue", 0))


def get_ticker_price(symbol: str):
    path = "v5/market/tickers"
    params = {"category": "linear", "symbol": symbol}
    resp = http_get(path, params)
    data = resp.json()
    return float(data.get("result", {}).get("list", [])[0].get("lastPrice", 0))


def get_position_qty(symbol: str, side: str):
    """
    Returns open position contract quantity for symbol and side (Buy/ Sell).
    """
    path = "v5/position/list"
    params = {"category": "linear", "symbol": symbol}
    resp = http_get(path, params)
    data = resp.json()
    for pos in data.get("result", {}).get("list", []):
        if pos.get("side", "").lower() == side.lower():
            return float(pos.get("size", 0))
    return 0.0


def set_leverage(symbol: str, long_leverage: int = LONG_LEVERAGE, short_leverage: int = SHORT_LEVERAGE):
    logger.info(f"Setting leverage: {symbol} L={long_leverage}, S={short_leverage}")
    body = {"category": "linear", "symbol": symbol,
            "buyLeverage": long_leverage, "sellLeverage": short_leverage}
    return http_post("v5/position/set-leverage", body).json()


def place_order(symbol: str, side: str, qty: float = 0, reduce_only: bool = False):
    """
    For Buy: uses 100%% of USDT balance * leverage.
    For exit (reduce_only): closes full position.
    """
    logger.info(f"Order: {side} {symbol}, qty_param={qty}, reduceOnly={reduce_only}")
    if side == "Buy" and not reduce_only:
        set_leverage(symbol)
        balance = get_wallet_balance()
        notional = balance * LONG_LEVERAGE
        min_c, step, min_n = get_symbol_info(symbol)
        price = get_ticker_price(symbol)
        qty_contracts = max(min_c, step * floor(notional / (price * step)))
    elif reduce_only:
        # exiting: get open contracts for opposite side
        closing_side = "Buy" if side == "Sell" else "Sell"
        qty_contracts = get_position_qty(symbol, closing_side)
        logger.debug(f"Closing position {closing_side}: contracts={qty_contracts}")
    else:
        qty_contracts = qty

    body = {"category": "linear", "symbol": symbol,
            "side": side, "orderType": "Market",
            "qty": str(qty_contracts),
            "timeInForce": "ImmediateOrCancel",
            "reduceOnly": reduce_only}
    resp = http_post("v5/order/create", body)
    return resp.json()


def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                  json={"chat_id": TELEGRAM_CHAT_ID, "text": text})

# ——— Flask App ———
app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    p = request.get_json(force=True)
    symbol, action = p.get("symbol"), p.get("side", "")
    qty = float(p.get("qty", 0))
    reduce_flag = action.lower().startswith("exit")
    side = "Buy" if "buy" in action.lower() else "Sell"
    result = place_order(symbol, side, qty, reduce_only=reduce_flag)
    send_telegram(f"{side} {symbol} qty={qty} → {result}")
    return {"status": "ok"}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
