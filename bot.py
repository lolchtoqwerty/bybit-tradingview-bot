# bot.py — Bybit TradingView Webhook Bot (Mainnet)
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
BYBIT_API_KEY    = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")
# По умолчанию подключаемся к основному API Bybit (Mainnet)
BASE_URL         = os.getenv("BYBIT_BASE_URL", "https://api.bybit.com")
RECV_WINDOW      = "5000"

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")

LONG_LEVERAGE    = 3  # leverage для открытия Buy
SHORT_LEVERAGE   = 1  # leverage для открытия Sell и для закрытия позиций

# ——— Logging Setup ———
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] [%(funcName)s] %(message)s"
)
logger = logging.getLogger(__name__)

# ——— Signature Helper ———
def sign_request(payload_str: str = "", query: str = ""):
    ts = str(int(time.time() * 1000))
    to_sign = ts + BYBIT_API_KEY + RECV_WINDOW + (payload_str or query)
    signature = hmac.new(
        BYBIT_API_SECRET.encode(), to_sign.encode(), hashlib.sha256
    ).hexdigest()
    return ts, signature

# ——— HTTP Helpers ———
def http_get(path: str, params: dict = None):
    url = f"{BASE_URL}/{path}"
    query = '&'.join(f"{k}={v}" for k, v in (params or {}).items())
    ts, sign = sign_request(query=query)
    headers = {
        "Content-Type":      "application/json",
        "X-BAPI-API-KEY":     BYBIT_API_KEY,
        "X-BAPI-TIMESTAMP":   ts,
        "X-BAPI-RECV-WINDOW": RECV_WINDOW,
        "X-BAPI-SIGN":        sign
    }
    resp = requests.get(url, headers=headers, params=params)
    logger.debug(f"GET {path}?{query} → {resp.status_code} {resp.text}")
    return resp


def http_post(path: str, body: dict):
    url = f"{BASE_URL}/{path}"
    payload_str = json.dumps(body, separators=(',', ':'), sort_keys=True)
    ts, sign = sign_request(payload_str=payload_str)
    headers = {
        "Content-Type":      "application/json",
        "X-BAPI-API-KEY":     BYBIT_API_KEY,
        "X-BAPI-TIMESTAMP":   ts,
        "X-BAPI-RECV-WINDOW": RECV_WINDOW,
        "X-BAPI-SIGN":        sign
    }
    resp = requests.post(url, headers=headers, data=payload_str)
    logger.debug(f"POST {path} {payload_str} → {resp.status_code} {resp.text}")
    return resp

# ——— Bybit Utilities ———
def get_wallet_balance(coin: str = "USDT", account_type: str = "UNIFIED") -> float:
    data = http_get("v5/account/wallet-balance", {"coin": coin, "accountType": account_type}).json()
    if data.get("retCode") != 0:
        return 0.0
    items = data["result"]["list"]
    return float(items[0].get("totalAvailableBalance", 0))


def get_symbol_info(symbol: str):
    data = http_get("v5/market/instruments-info", {"category": "linear", "symbol": symbol}).json()
    filt = data["result"]["list"][0]["lotSizeFilter"]
    return float(filt.get("minOrderQty", 0)), float(filt.get("qtyStep", 1)), float(filt.get("minNotionalValue", 0))


def get_ticker_price(symbol: str) -> float:
    data = http_get("v5/market/tickers", {"category": "linear", "symbol": symbol}).json()
    return float(data["result"]["list"][0].get("lastPrice", 0))


def get_position_qty(symbol: str, side: str) -> float:
    data = http_get("v5/position/list", {"category": "linear", "symbol": symbol}).json()
    for pos in data["result"]["list"]:
        if pos.get("side", "").lower() == side.lower():
            return float(pos.get("size", 0))
    return 0.0


def set_leverage(symbol: str):
    # Используем snake_case параметров и указываем position_idx
    body = {
        "category":     "linear",
        "symbol":       symbol,
        "buy_leverage": LONG_LEVERAGE,
        "sell_leverage":SHORT_LEVERAGE,
        "position_idx": 0
    }
    return http_post("v5/position/set-leverage", body).json()


def get_executions(symbol: str, order_id: str):
    data = http_get(
        "v5/execution/list",
        {"category": "linear", "symbol": symbol, "orderId": order_id}
    ).json()
    return data.get("result", {}).get("list", [])

# ——— Core Order Functions ———
def place_order(symbol: str, side: str, qty: float = 0, reduce_only: bool = False) -> dict:
    # Открытие Buy
    if side == 'Buy' and not reduce_only:
        set_leverage(symbol)
        balance = get_wallet_balance()
        min_c, step, _ = get_symbol_info(symbol)
        price = get_ticker_price(symbol)
        qty_contracts = max(min_c, step * floor((balance * LONG_LEVERAGE) / (price * step)))
        res = http_post("v5/order/create", {
            "category":"linear","symbol":symbol,
            "side":"Buy","orderType":"Market",
            "qty":str(qty_contracts),"timeInForce":"ImmediateOrCancel"
        })
        return {"result": res.json(), "executed": qty_contracts, "remaining": None}

    # Открытие Sell (шорт)
    if side == 'Sell' and not reduce_only:
        set_leverage(symbol)
        balance = get_wallet_balance()
        min_c, step, _ = get_symbol_info(symbol)
        price = get_ticker_price(symbol)
        qty_contracts = max(min_c, step * floor((balance * SHORT_LEVERAGE) / (price * step)))
        res = http_post("v5/order/create", {
            "category":"linear","symbol":symbol,
            "side":"Sell","orderType":"Market",
            "qty":str(qty_contracts),"timeInForce":"ImmediateOrCancel"
        })
        return {"result": res.json(), "executed": qty_contracts, "remaining": None}

    # Закрытие позиции по reduce_only (Sell для Buy или Buy для Sell)
    if reduce_only:
        opposite = 'Sell' if side=='Buy' else 'Buy'
        qty_close = get_position_qty(symbol, 'Buy' if side=='Sell' else 'Sell')
        res = http_post("v5/order/create", {
            "category":"linear","symbol":symbol,
            "side":opposite,"orderType":"Market",
            "qty":str(qty_close),"timeInForce":"ImmediateOrCancel",
            "reduce_only":True
        })
        order_id = res.json().get("result", {}).get("orderId", "")
        exec_list = get_executions(symbol, order_id) if order_id else []
        actual = sum(float(evt.get("execQty", 0)) for evt in exec_list)
        remaining = get_position_qty(symbol, side)
        return {"result": res.json(), "executed": actual, "remaining": remaining}

    # В остальных случаях используем переданный qty
    res = http_post("v5/order/create", {
        "category":"linear","symbol":symbol,
        "side":side,"orderType":"Market",
        "qty":str(qty),"timeInForce":"ImmediateOrCancel",
        "reduce_only":reduce_only
    })
    return {"result": res.json(), "executed": qty, "remaining": None}

# ——— Telegram ———
def send_telegram(text: str):
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text}
        )

# ——— Flask App ———
app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    payload     = request.get_json(force=True)
    symbol      = payload.get('symbol')
    action      = payload.get('side', '')
    qty_param   = float(payload.get('qty', 0))
    reduce_flag = action.lower().startswith('exit')
    side        = 'Buy' if 'buy' in action.lower() else 'Sell'

    order = place_order(symbol, side, qty_param, reduce_only=reduce_flag)
    msg   = f"{side} {symbol} executed={order['executed']}"
    if order.get('remaining') is not None:
        msg += f" remaining={order['remaining']}"
    msg  += f" → {order['result']}"
    send_telegram(msg)
    return {"status": "ok"}

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 10000)))
