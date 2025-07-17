import os
import time
import json
import hmac
import hashlib
import logging
import requests
from flask import Flask, request, jsonify
from math import floor

# — Configuration —
BYBIT_API_KEY    = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")
BASE_URL         = os.getenv("BYBIT_BASE_URL", "https://api.bybit.com")
RECV_WINDOW      = "5000"

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")

LONG_LEVERAGE    = 3
SHORT_LEVERAGE   = 1
USAGE_RATIO      = 0.95  # используем 95% баланса

# — Logging Setup —
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger()

# — HTTP Helpers —
def sign_request(payload: str = "", query: str = ""):
    ts = str(int(time.time() * 1000))
    msg = ts + BYBIT_API_KEY + RECV_WINDOW + (payload or query)
    sig = hmac.new(BYBIT_API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return ts, sig

def api_call(method, path, params=None, body=None):
    url = f"{BASE_URL}/{path}"
    payload = json.dumps(body, separators=(",", ":"), sort_keys=True) if body is not None else ""
    query = "&".join(f"{k}={v}" for k,v in (params or {}).items())
    ts, sig = sign_request(payload, query)
    headers = {
        "Content-Type": "application/json",
        "X-BAPI-API-KEY": BYBIT_API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": RECV_WINDOW,
        "X-BAPI-SIGN": sig
    }
    logger.info("API call: %s %s | params: %s | body: %s", method, path, params, body)
    resp = requests.request(method, url, headers=headers, params=params, data=payload)
    resp.raise_for_status()
    data = resp.json()
    logger.info("API response: %s %s | %s", method, path, data)
    return data

# — Bybit Data —
def get_wallet_balance():
    data = api_call('GET', "v5/account/wallet-balance", {"coin": "USDT", "accountType": "UNIFIED"})
    return float(data["result"]["list"][0]["totalAvailableBalance"])

def get_symbol_filters(sym):
    info = api_call('GET', "v5/market/instruments-info", {"category":"linear","symbol":sym})
    f = info["result"]["list"][0]
    lot = f["lotSizeFilter"]
    price = f["priceFilter"]
    return {
        "minQty": float(lot["minOrderQty"]),
        "step": float(lot["qtyStep"]),
        "minNotional": float(lot.get("minNotionalValue", 0)),
    }

def get_ticker_price(sym):
    data = api_call('GET', "v5/market/tickers", {"category":"linear","symbol":sym})
    return float(data["result"]["list"][0]["lastPrice"])

def get_positions(sym):
    data = api_call('GET', "v5/position/list", {"category":"linear","symbol":sym})
    return data["result"]["list"]

def get_executions(sym, oid):
    data = api_call('GET', "v5/execution/list", {"category":"linear","symbol":sym,"orderId":oid})
    return data["result"]["list"]

# — Telegram —
def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    logger.info("Sending Telegram message: %s", msg)
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}
    )

# — Order Logic —
def compute_qty(balance, price, leverage, filters):
    raw = (balance * leverage * USAGE_RATIO) / price
    step = filters["step"]
    qty = step * floor(raw / step)
    if qty < filters["minQty"]:
        return 0
    return qty

def place_order(sym, side, qty, reduce_only=False):
    if qty <= 0:
        logger.error("Qty too small, skip order: %s", qty)
        return None, []
    body = {"category":"linear","symbol":sym, "side":side, "orderType":"Market", "qty":str(qty), "timeInForce":"ImmediateOrCancel"}
    if reduce_only:
        body["reduce_only"] = True
    resp = api_call('POST', "v5/order/create", body=body)
    oid = resp["result"].get("orderId")
    if not oid:
        logger.error("Order create failed: %s", resp)
        return None, []
    logger.info("Order created: %s %s", side, oid)
    return oid, get_executions(sym, oid)

def close_position(sym, side):
    pos = next((p for p in get_positions(sym) if p["side"]==side), None)
    if not pos:
        return
    size  = float(pos["size"])
    entry = float(pos["avgPrice"])
    _, execs = place_order(sym, "Sell" if side=="Buy" else "Buy", size, reduce_only=True)
    total = sum(float(e["execQty"]) for e in execs)
    avg   = sum(float(e["execQty"])*float(e["execPrice"]) for e in execs)/total if total else entry
    fees  = sum(float(e.get("execFee",0)) for e in execs)
    bal   = get_wallet_balance()
    pnl   = ((avg-entry)*size - fees) if side=="Buy" else ((entry-avg)*size - fees)
    pct   = pnl/bal*100 if bal else 0
    arrow = "🔹" if side=="Buy" else "🔻"
    send_telegram(f"{arrow} {side} closed: {sym}\n• PnL: {pnl:.4f} USDT ({pct:+.2f}%)")

def open_position(sym, side, leverage):
    if any(p["side"]==side for p in get_positions(sym)):
        return
    balance = get_wallet_balance()
    price   = get_ticker_price(sym)
    filt    = get_symbol_filters(sym)
    qty     = compute_qty(balance, price, leverage, filt)
    oid, _  = place_order(sym, side, qty)
    arrow   = "🔹" if side=="Buy" else "🔻"
    send_telegram(f"{arrow} {side} opened: {sym} @ {price}")

def close_and_open(sym, target_side, leverage):
    opposite  = "Buy" if target_side=="Sell" else "Sell"
    close_position(sym, opposite)
    time.sleep(0.5)  # даём бирже время обновить позицию
    open_position(sym, target_side, leverage)

# — Flask App —
app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True)
        sym = data.get("symbol")
        side = data.get("side", "").lower()
        leverage = data.get("leverage")

        if side in ["buy", "sell"]:
            target_side = "Buy" if side == "buy" else "Sell"
            lev = int(leverage) if leverage else (LONG_LEVERAGE if target_side == "Buy" else SHORT_LEVERAGE)
            close_and_open(sym, target_side, lev)
            logger.info(f"Processed {side} for {sym} with leverage {lev}")
        elif "exit" in side:
            logger.info(f"Ignoring exit alert: {side} for {sym}")
        else:
            logger.error(f"Unknown side: {side} for {sym}")
        
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
