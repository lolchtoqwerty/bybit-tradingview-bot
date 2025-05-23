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

LONG_LEVERAGE = 3   # leverage multiplier for Buy
SHORT_LEVERAGE = 1  # leverage multiplier for Sell

# ——— Logging Setup ———
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] [%(funcName)s] %(message)s"
)
logger = logging.getLogger(__name__)

# ——— Signature Helper ———
def sign_request(path: str, payload_str: str = "", query: str = ""):
    ts = str(int(time.time() * 1000))
    to_sign = ts + BYBIT_API_KEY + (payload_str or query)
    signature = hmac.new(
        BYBIT_API_SECRET.encode(), to_sign.encode(), hashlib.sha256
    ).hexdigest()
    return ts, signature

# ——— HTTP Helpers ———
def http_get(path: str, params: dict = None):
    url = f"{BASE_URL}/{path}"
    query = '&'.join(f"{k}={v}" for k, v in (params or {}).items())
    ts, sign = sign_request(path, query=query)
    headers = {"X-BAPI-API-KEY": BYBIT_API_KEY, "X-BAPI-TIMESTAMP": ts, "X-BAPI-SIGN": sign}
    resp = requests.get(url, headers=headers, params=params)
    logger.debug(f"GET {path}?{query} → {resp.status_code} {resp.text}")
    return resp


def http_post(path: str, body: dict):
    url = f"{BASE_URL}/{path}"
    payload_str = json.dumps(body, separators=(',', ':'), sort_keys=True)
    ts, sign = sign_request(path, payload_str=payload_str)
    headers = {
        "Content-Type": "application/json",
        "X-BAPI-API-KEY": BYBIT_API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-SIGN": sign
    }
    resp = requests.post(url, headers=headers, data=payload_str)
    logger.debug(f"POST {path} {payload_str} → {resp.status_code} {resp.text}")
    return resp

# ——— Bybit Utilities ———
def get_wallet_balance(coin: str = "USDT", account_type: str = "UNIFIED") -> float:
    data = http_get("v5/account/wallet-balance", {"coin": coin, "accountType": account_type}).json()
    if data.get("retCode") != 0:
        return 0.0
    items = data.get("result", {}).get("list", [])
    return float(items[0].get("totalAvailableBalance", 0)) if items else 0.0


def get_symbol_info(symbol: str):
    data = http_get("v5/market/instruments-info", {"category": "linear", "symbol": symbol}).json()
    filt = data.get("result", {}).get("list", [])[0].get("lotSizeFilter", {})
    return float(filt.get("minOrderQty", 0)), float(filt.get("qtyStep", 1)), float(filt.get("minNotionalValue", 0))


def get_ticker_price(symbol: str) -> float:
    data = http_get("v5/market/tickers", {"category": "linear", "symbol": symbol}).json()
    return float(data.get("result", {}).get("list", [])[0].get("lastPrice", 0))


def get_position_qty(symbol: str, side: str) -> float:
    data = http_get("v5/position/list", {"category": "linear", "symbol": symbol}).json()
    for pos in data.get("result", {}).get("list", []):
        if pos.get("side", "").lower() == side.lower():
            return float(pos.get("size", 0))
    return 0.0


def set_leverage(symbol: str):
    return http_post(
        "v5/position/set-leverage",
        {"category": "linear", "symbol": symbol, "buyLeverage": LONG_LEVERAGE, "sellLeverage": SHORT_LEVERAGE}
    ).json()


def place_order(symbol: str, side: str, qty: float = 0, reduce_only: bool = False) -> dict:
    """
    Executes a market order on linear perpetual futures.
    - For Buy (reduce_only=False): opens position with 100% balance × leverage.
    - For Close (reduce_only=True): closes entire opposite position using its size.

    Returns:
      - result: raw API response
      - executed: number of contracts sent
      - remaining: remaining size after close (None for opens)
    """
    # Determine contract quantity
    if side == 'Buy' and not reduce_only:
        set_leverage(symbol)
        balance = get_wallet_balance()
        min_c, step, _ = get_symbol_info(symbol)
        price = get_ticker_price(symbol)
        qty_contracts = max(min_c, step * floor((balance * LONG_LEVERAGE) / (price * step)))
    elif reduce_only:
        # closing: fetch current open contracts on opposite side
        opposite = 'Buy' if side == 'Sell' else 'Sell'
        qty_contracts = get_position_qty(symbol, opposite)
    else:
        qty_contracts = qty

    # Build order payload
    body = {
        "category": "linear",
        "symbol": symbol,
        "side": side,
        "orderType": "Market",
        "qty": str(qty_contracts),
        "timeInForce": "ImmediateOrCancel",
        "reduceOnly": reduce_only
    }

    # Send order once
    resp = http_post("v5/order/create", body)
    result = resp.json()
    executed = qty_contracts

    # Check remaining if closed
    remaining = None
    if reduce_only:
        opposite = 'Buy' if side == 'Sell' else 'Sell'
        remaining = get_position_qty(symbol, opposite)

    return {"result": result, "executed": executed, "remaining": remaining}

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 10000)))
