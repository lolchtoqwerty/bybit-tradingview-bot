import os
import time
import hmac
import hashlib
from flask import Flask, request, jsonify
import requests

# Load config from environment
API_KEY    = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")
BASE_URL   = os.getenv("BYBIT_BASE_URL", "https://api-testnet.bybit.com")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Telegram helper
def send_telegram(message: str):
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
        try:
            requests.post(url, json=payload)
        except Exception as e:
            print(f"Telegram error: {e}")

# Sign parameters for Bybit
def sign_params(params: dict) -> str:
    sign_str = '&'.join(f"{k}={v}" for k,v in sorted(params.items()))
    return hmac.new(API_SECRET.encode(), sign_str.encode(), hashlib.sha256).hexdigest()

# Fetch wallet balance (USDT)
def get_balance(currency="USDT"):
    path   = "/v2/private/wallet/balance"
    ts     = int(time.time() * 1000)
    params = {"api_key": API_KEY, "timestamp": ts}
    params["sign"] = sign_params(params)
    r = requests.get(BASE_URL + path, params=params)
    data = r.json().get("result", {})
    return data.get(currency, {}).get("wallet_balance")

# Place market order
def place_order(symbol: str, side: str, qty: float, reduce_only: bool=False):
    print("=== Place Order Called ===", symbol, side, qty, "reduce_only=", reduce_only)
    path   = "/v2/private/order/create"
    ts     = int(time.time() * 1000)
    params = {
        "api_key": API_KEY,
        "symbol": symbol,
        "side": side.upper(),
        "order_type": "Market",
        "qty": qty,
        "time_in_force": "GoodTillCancel",
        "timestamp": ts,
        "reduce_only": str(reduce_only).lower()
    }
    params["sign"] = sign_params(params)
    url = BASE_URL + path
    resp = requests.post(url, params=params, timeout=10)
    res = resp.json()
    send_telegram(f"{side} {symbol} qty={qty} → {res}")
    return res

# Initialize Flask app
app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    # Debug incoming webhook payload
    print("=== GOT WEBHOOK ===", request.data)
    try:
        data = request.get_json(force=True)
        print("Parsed JSON:", data)
    except Exception as e:
        print("JSON parse error:", e)
        return jsonify({"error": "invalid JSON"}), 400

    symbol = data.get('symbol')
    side   = data.get('side')
    qty    = data.get('qty', 1)

    # Execute based on side
    if side == 'buy':
        place_order(symbol, 'Buy', qty)
    elif side == 'sell':
        place_order(symbol, 'Sell', qty)
    elif side == 'exit long':
        place_order(symbol, 'Sell', qty, reduce_only=True)
        bal = get_balance(symbol)
        send_telegram(f"Balance after exit long: {bal} {symbol}")
    elif side == 'exit short':
        place_order(symbol, 'Buy', qty, reduce_only=True)
        bal = get_balance(symbol)
        send_telegram(f"Balance after exit short: {bal} {symbol}")
    else:
        print("Unknown side:", side)
        return jsonify({"error": "unknown side"}), 400

    return jsonify({"status": "ok"})

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
