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

def sign_request(payload_str: str = "", query: str = ""):
    ts = str(int(time.time() * 1000))
    to_sign = ts + BYBIT_API_KEY + RECV_WINDOW + (payload_str or query)
    sig   = hmac.new(BYBIT_API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    return ts, sig

def http_get(path, params=None):
    url = f"{BASE_URL}/{path}"
    query = "&".join(f"{k}={v}" for k,v in (params or {}).items())
    ts, sig = sign_request(query=query)
    headers = {
        "Content-Type":"application/json",
        "X-BAPI-API-KEY":BYBIT_API_KEY,
        "X-BAPI-TIMESTAMP":ts,
        "X-BAPI-RECV-WINDOW":RECV_WINDOW,
        "X-BAPI-SIGN":sig
    }
    r = requests.get(url, headers=headers, params=params); r.raise_for_status()
    return r.json()

def http_post(path, body):
    url = f"{BASE_URL}/{path}"
    payload = json.dumps(body, separators=(",",":"), sort_keys=True)
    ts, sig = sign_request(payload_str=payload)
    headers = {
        "Content-Type":"application/json",
        "X-BAPI-API-KEY":BYBIT_API_KEY,
        "X-BAPI-TIMESTAMP":ts,
        "X-BAPI-RECV-WINDOW":RECV_WINDOW,
        "X-BAPI-SIGN":sig
    }
    r = requests.post(url, headers=headers, data=payload); r.raise_for_status()
    return r.json()

def get_wallet_balance():
    d = http_get("v5/account/wallet-balance", {"coin":"USDT","accountType":"UNIFIED"})
    return float(d["result"]["list"][0]["totalAvailableBalance"]) if d.get("retCode")==0 else 0.0

def get_symbol_info(sym):
    d = http_get("v5/market/instruments-info", {"category":"linear","symbol":sym})
    f = d["result"]["list"][0]["lotSizeFilter"]
    return float(f["minOrderQty"]), float(f["qtyStep"])

def get_ticker_price(sym):
    d = http_get("v5/market/tickers", {"category":"linear","symbol":sym})
    return float(d["result"]["list"][0]["lastPrice"])

def get_positions(sym):
    d = http_get("v5/position/list", {"category":"linear","symbol":sym})
    return d.get("result",{}).get("list",[])

def get_executions(sym, oid):
    d = http_get("v5/execution/list", {"category":"linear","symbol":sym,"orderId":oid})
    return d.get("result",{}).get("list",[])

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                  json={"chat_id":TELEGRAM_CHAT_ID,"text":msg})

app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json(force=True)
    sym  = data.get("symbol")
    side = data.get("side","").lower()
    logger.info("▶ Webhook: %s", data)
    if not sym or not side: return jsonify(status="ignored"),200

    # EXIT LONG
    if side=="exit long":
        pos = next((p for p in get_positions(sym) if p["side"]=="Buy"), None)
        if pos:
            qty = float(pos["size"]); entry = float(pos["avgPrice"]); bal = get_wallet_balance()
            r = http_post("v5/order/create", {
                "category":"linear","symbol":sym,"side":"Sell",
                "orderType":"Market","qty":str(qty),
                "timeInForce":"ImmediateOrCancel","reduce_only":True
            })
            execs = get_executions(sym, r["result"]["orderId"])
            exit_p = entry
            if execs:
                tot = sum(float(e["execQty"])*float(e["execPrice"]) for e in execs)
                cnt = sum(float(e["execQty"]) for e in execs)
                exit_p = tot/cnt
            pnl = (exit_p-entry)*qty - sum(float(e.get("execFee",0)) for e in execs)
            pct = pnl/bal*100 if bal>0 else 0
            msg = f"🔹 Лонг закрыт: {sym}\n• PnL: {pnl:.4f} USDT ({pct:+.2f}%)"
            send_telegram(msg)
        return jsonify(status="ok"),200

    # EXIT SHORT
    if side=="exit short":
        pos = next((p for p in get_positions(sym) if p["side"]=="Sell"), None)
        if pos:
            qty = float(pos["size"]); entry = float(pos["avgPrice"]); bal = get_wallet_balance()
            r = http_post("v5/order/create", {
                "category":"linear","symbol":sym,"side":"Buy",
                "orderType":"Market","qty":str(qty),
                "timeInForce":"ImmediateOrCancel","reduce_only":True
            })
            execs = get_executions(sym, r["result"]["orderId"])
            exit_p = entry
            if execs:
                tot = sum(float(e["execQty"])*float(e["execPrice"]) for e in execs)
                cnt = sum(float(e["execQty"]) for e in execs)
                exit_p = tot/cnt
            pnl = (entry-exit_p)*qty - sum(float(e.get("execFee",0)) for e in execs)
            pct = pnl/bal*100 if bal>0 else 0
            msg = f"🔻 Шорт закрыт: {sym}\n• PnL: {pnl:.4f} USDT ({pct:+.2f}%)"
            send_telegram(msg)
        return jsonify(status="ok"),200

    # SELL
    if side=="sell":
        if any(p["side"]=="Buy" for p in get_positions(sym)):
            return jsonify(status="ignored",reason="waiting exit long"),200
        bal = get_wallet_balance(); mn, st = get_symbol_info(sym); price = get_ticker_price(sym)
        qty = max(mn, st*floor((bal*SHORT_LEVERAGE*USAGE_RATIO)/(price*st)))
        r = http_post("v5/order/create", {
            "category":"linear","symbol":sym,"side":"Sell",
            "orderType":"Market","qty":str(qty),"timeInForce":"ImmediateOrCancel"
        })
        if r.get("retCode")==110007:
            send_telegram(f"❌ Не открыл шорт {sym}: недостаточно баланса")
        else:
            send_telegram(f"🔻 Шорт открыт: {sym} @ {price}")
        return jsonify(status="ok"),200

    # BUY
    if side=="buy":
        if any(p["side"]=="Sell" for p in get_positions(sym)):
            return jsonify(status="ignored",reason="waiting exit short"),200
        bal = get_wallet_balance(); mn, st = get_symbol_info(sym); price = get_ticker_price(sym)
        qty = max(mn, st*floor((bal*LONG_LEVERAGE*USAGE_RATIO)/(price*st)))
        r = http_post("v5/order/create", {
            "category":"linear","symbol":sym,"side":"Buy",
            "orderType":"Market","qty":str(qty),"timeInForce":"ImmediateOrCancel"
        })
        if r.get("retCode")==110007:
            send_telegram(f"❌ Не открыл лонг {sym}: недостаточно баланса")
        else:
            send_telegram(f"🔹 Лонг открыт: {sym} @ {price}")
        return jsonify(status="ok"),200

    return jsonify(status="ignored"),200

if __name__=='__main__':
    app.run(host='0.0.0.0',port=int(os.getenv('PORT',10000)))
