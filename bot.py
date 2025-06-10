import os, time, json, hmac, hashlib, logging, requests
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
    query = "&".join(f"{k}={v}" for k, v in (params or {}).items())
    ts, sig = sign_request(payload_str=payload, query=query)
    headers = {
        "Content-Type": "application/json",
        "X-BAPI-API-KEY": BYBIT_API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": RECV_WINDOW,
        "X-BAPI-SIGN": sig
    }
    resp = requests.request(method, url, headers=headers, params=params, data=payload)
    resp.raise_for_status()
    return resp.json()

# — Bybit Data —
def get_wallet_balance():
    data = api_call('GET', "v5/account/wallet-balance", {"coin": "USDT", "accountType": "UNIFIED"})
    try:
        return float(data["result"]["list"][0]["totalAvailableBalance"])
    except:
        return 0.0


def get_symbol_info(sym):
    data = api_call('GET', "v5/market/instruments-info", {"category": "linear", "symbol": sym})
    filt = data["result"]["list"][0]["lotSizeFilter"]
    return float(filt["minOrderQty"]), float(filt["qtyStep"])


def get_ticker_price(sym):
    data = api_call('GET', "v5/market/tickers", {"category": "linear", "symbol": sym})
    return float(data["result"]["list"][0]["lastPrice"])


def get_positions(sym):
    data = api_call('GET', "v5/position/list", {"category": "linear", "symbol": sym})
    return data.get("result", {}).get("list", [])


def get_executions(sym, oid):
    data = api_call('GET', "v5/execution/list", {"category": "linear", "symbol": sym, "orderId": oid})
    return data.get("result", {}).get("list", [])

# — Telegram —
def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}
    )

# — Order Logic —
def compute_qty(balance, price, leverage, mn, step):
    raw = (balance * leverage * USAGE_RATIO) / price
    qty = max(mn, step * floor(raw / step))
    return qty


def place_order(sym, side, qty, reduce_only=False):
    body = {
        "category": "linear",
        "symbol": sym,
        "side": side,
        "orderType": "Market",
        "qty": str(qty),
        "timeInForce": "ImmediateOrCancel"
    }
    if reduce_only:
        body["reduce_only"] = True
    resp = api_call('POST', "v5/order/create", body=body)
    return resp.get("result", {}).get("orderId"), get_executions(sym, resp["result"]["orderId"])


def close_and_open(sym, close_side, open_side, open_lev):
    positions = get_positions(sym)
    pos = next((p for p in positions if p["side"] == close_side), None)
    if not pos:
        return

    size = float(pos["size"])
    entry = float(pos["avgPrice"])
    bal = get_wallet_balance()

    # Close
    oid, execs = place_order(sym, "Sell" if close_side=="Buy" else "Buy", size, reduce_only=True)
    total_qty = sum(float(e["execQty"]) for e in execs)
    avg_price = sum(float(e["execQty"])*float(e["execPrice"]) for e in execs) / total_qty if execs else entry
    fees = sum(float(e.get("execFee",0)) for e in execs)
    pnl = (avg_price - entry)*size if close_side=="Buy" else (entry - avg_price)*size
    pnl -= fees
    pct = pnl / bal * 100 if bal else 0
    arrow = "🔹" if close_side=="Buy" else "🔻"
    send_telegram(f"{arrow} {close_side} closed: {sym}\n• PnL: {pnl:.4f} USDT ({pct:+.2f}%)")

    # Open opposite
    balance = get_wallet_balance()
    mn, step = get_symbol_info(sym)
    price = get_ticker_price(sym)
    qty = compute_qty(balance, price, open_lev, mn, step)
    oid2, _ = place_order(sym, open_side, qty)
    arrow2 = "🔹" if open_side=="Buy" else "🔻"
    send_telegram(f"{arrow2} {open_side} opened: {sym} @ {price}")

# — Flask App —
app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json(force=True)
    sym = data.get("symbol")
    side = data.get("side", "").lower()
    logger.info("▶ Webhook received: %s", data)
    if not sym or not side:
        return jsonify(status="ignored"), 200

    actions = {
        "exit long": lambda: close_and_open(sym, "Buy", "Sell", SHORT_LEVERAGE),
        "exit short": lambda: close_and_open(sym, "Sell", "Buy", LONG_LEVERAGE),
        "buy": lambda: close_and_open(sym, None, "Buy", LONG_LEVERAGE) if not any(p["side"]=="Buy" for p in get_positions(sym)) else None,
        "sell": lambda: close_and_open(sym, None, "Sell", SHORT_LEVERAGE) if not any(p["side"]=="Sell" for p in get_positions(sym)) else None
    }

    action = actions.get(side)
    if action:
        action()
        return jsonify(status="ok"), 200

    return jsonify(status="ignored"), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 10000)))
