# bot.py — Bybit TradingView Webhook Bot
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
BASE_URL         = os.getenv("BYBIT_BASE_URL", "https://api-testnet.bybit.com")
RECV_WINDOW      = "5000"

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")

LONG_LEVERAGE  = 3
SHORT_LEVERAGE = 1

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
    return float(items[0]["totalAvailableBalance"])

def get_symbol_info(symbol: str):
    data = http_get("v5/market/instruments-info", {"category": "linear", "symbol": symbol}).json()
    filt = data["result"]["list"][0]["lotSizeFilter"]
    return float(filt["minOrderQty"]), float(filt["qtyStep"]), float(filt["minNotionalValue"])

def get_ticker_price(symbol: str) -> float:
    data = http_get("v5/market/tickers", {"category": "linear", "symbol": symbol}).json()
    return float(data["result"]["list"][0]["lastPrice"])

def get_position_qty(symbol: str, side: str) -> float:
    data = http_get("v5/position/list", {"category": "linear", "symbol": symbol}).json()
    for pos in data["result"]["list"]:
        if pos["side"].lower() == side.lower():
            return float(pos["size"])
    return 0.0

def set_leverage(symbol: str):
    return http_post(
        "v5/position/set-leverage",
        {"category": "linear", "symbol": symbol, "buyLeverage": LONG_LEVERAGE, "sellLeverage": SHORT_LEVERAGE}
    ).json()

def get_executions(symbol: str, order_id: str):
    data = http_get(
        "v5/execution/list",
        {"category": "linear", "symbol": symbol, "orderId": order_id}
    ).json()
    return data.get("result", {}).get("list", [])

def place_order(symbol: str, side: str, qty: float = 0, reduce_only: bool = False) -> dict:
    # вычисляем контрактный объём
    if side == 'Buy' and not reduce_only:
        set_leverage(symbol)
        balance = get_wallet_balance()
        min_c, step, _ = get_symbol_info(symbol)
        price = get_ticker_price(symbol)
        qty_contracts = max(min_c, step * floor((balance * LONG_LEVERAGE) / (price * step)))
    elif reduce_only:
        opposite = 'Buy' if side == 'Sell' else 'Sell'
        qty_contracts = get_position_qty(symbol, opposite)
    else:
        qty_contracts = qty

    body = {
        "category":   "linear",
        "symbol":     symbol,
        "side":       side,
        "orderType":  "Market",
        "qty":        str(qty_contracts),
        "timeInForce":"ImmediateOrCancel",
        "reduce_only": reduce_only
    }

    # создаём ордер
    resp   = http_post("v5/order/create", body)
    result = resp.json()
    order_id = result.get("result", {}).get("orderId", "")

    # если это закрытие позиции — проверяем реальные исполнившиеся объёмы
    if reduce_only and order_id:
        exec_list = get_executions(symbol, order_id)
        actual = sum(float(evt.get("execQty", 0)) for evt in exec_list)
        remaining = get_position_qty(symbol, 'Buy' if side=='Sell' else 'Sell')
        executed = actual
    else:
        executed  = qty_contracts
        remaining = None

    return {
        "result":    result,
        "executed":  executed,
        "remaining": remaining
    }

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
    payload    = request.get_json(force=True)
    symbol     = payload.get('symbol')
    action     = payload.get('side', '')
    qty_param  = float(payload.get('qty', 0))
    reduce_flag= action.lower().startswith('exit')
    side       = 'Buy' if 'buy' in action.lower() else 'Sell'

    order = place_order(symbol, side, qty_param, reduce_only=reduce_flag)
    msg   = f"{side} {symbol} executed={order['executed']}"
    if order['remaining'] is not None:
        msg += f" remaining={order['remaining']}"
    msg  += f" → {order['result']}"
    send_telegram(msg)
    return {"status": "ok"}

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 10000)))
