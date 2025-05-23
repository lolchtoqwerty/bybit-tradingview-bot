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
    return resp


def http_post(path: str, body: dict):
    url = f"{BASE_URL}/{path}"
    payload_str = json.dumps(body, separators=(',', ':'), sort_keys=True)
    ts, sign = sign_request(path, payload_str=payload_str)
    headers = {"Content-Type": "application/json", "X-BAPI-API-KEY": BYBIT_API_KEY,
               "X-BAPI-TIMESTAMP": ts, "X-BAPI-SIGN": sign}
    resp = requests.post(url, headers=headers, data=payload_str)
    return resp

# ——— Bybit Utilities ———
def get_wallet_balance(coin: str = "USDT", account_type: str = "UNIFIED") -> float:
    path = "v5/account/wallet-balance"
    params = {"coin": coin, "accountType": account_type}
    data = http_get(path, params).json()
    if data.get("retCode") != 0:
        return 0.0
    items = data.get("result", {}).get("list", [])
    if not items:
        return 0.0
    return float(items[0].get("totalAvailableBalance", 0))


def get_symbol_info(symbol: str):
    path = "v5/market/instruments-info"
    params = {"category": "linear", "symbol": symbol}
    f = http_get(path, params).json().get("result", {}).get("list", [])[0].get("lotSizeFilter", {})
    return float(f.get("minOrderQty", 0)), float(f.get("qtyStep", 1)), float(f.get("minNotionalValue", 0))


def get_ticker_price(symbol: str) -> float:
    path = "v5/market/tickers"
    params = {"category": "linear", "symbol": symbol}
    return float(http_get(path, params).json().get("result", {}).get("list", [])[0].get("lastPrice", 0))


def get_position_qty(symbol: str, side: str) -> float:
    path = "v5/position/list"
    params = {"category": "linear", "symbol": symbol}
    for pos in http_get(path, params).json().get("result", {}).get("list", []):
        if pos.get("side", "").lower() == side.lower():
            return float(pos.get("size", 0))
    return 0.0


def set_leverage(symbol: str):
    body = {"category": "linear", "symbol": symbol,
            "buyLeverage": LONG_LEVERAGE, "sellLeverage": SHORT_LEVERAGE}
    return http_post("v5/position/set-leverage", body).json()


def place_order(symbol: str, side: str, qty: float = 0, reduce_only: bool = False) -> dict:
    """
    Executes market order.
    Returns dict with response and executed contract qty.
    """
    # Determine contract qty
    if side == 'Buy' and not reduce_only:
        set_leverage(symbol)
        balance = get_wallet_balance()
        notional = balance * LONG_LEVERAGE
        min_c, step, _ = get_symbol_info(symbol)
        price = get_ticker_price(symbol)
        qty_contracts = max(min_c, step * floor(notional / (price * step)))
    elif reduce_only:
        # exit: close opposite open
        close_side = 'Buy' if side == 'Sell' else 'Sell'
        qty_contracts = get_position_qty(symbol, close_side)
    else:
        qty_contracts = qty

    # Place order
    body = {"category": "linear", "symbol": symbol, "side": side,
            "orderType": "Market", "qty": str(qty_contracts),
            "timeInForce": "ImmediateOrCancel", "reduceOnly": reduce_only}
    resp = http_post("v5/order/create", body)
    result = resp.json()
    return {"result": result, "execQty": qty_contracts}


def send_telegram(text: str):
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      json={"chat_id": TELEGRAM_CHAT_ID, "text": text})

# ——— Flask App ———
app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    p = request.get_json(force=True)
    symbol = p.get('symbol')
    action = p.get('side', '')
    qty_param = float(p.get('qty', 0))
    reduce_flag = action.lower().startswith('exit')
    side = 'Buy' if 'buy' in action.lower() else 'Sell'

    order = place_order(symbol, side, qty_param, reduce_only=reduce_flag)
    msg = f"{side} {symbol} execQty={order['execQty']} → {order['result']}"
    send_telegram(msg)
    return {"status": "ok"}

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 10000)))
