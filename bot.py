import os
import time
from flask import Flask, request, jsonify
from pybit.unified_trading import HTTP
import requests

# Load config from environment
API_KEY    = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Initialize Bybit Unified Trading HTTP client for USDT Perpetual
client = HTTP(
    testnet=True,
    api_key=API_KEY,
    api_secret=API_SECRET,
    recv_window=5000
)

# Telegram helper
def send_telegram(message: str):
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
        try:
            requests.post(url, json=payload)
        except Exception as e:
            print(f"Telegram error: {e}")

# Fetch wallet balance for USDT
def get_balance(currency="USDT"):
    try:
        resp = client.get_wallet_balance(category="linear", coin=currency)
        return resp.get('result', {}).get(currency, {}).get('wallet_balance')
    except Exception as e:
        print(f"Balance fetch error: {e}")
        return None

# Place market order using Unified Trading client
# symbol example: "SUSDT"
def place_order(symbol: str, side: str, qty: float, reduce_only: bool=False):
    print("=== Place Order Called ===", symbol, side, qty, "reduce_only=", reduce_only)
    try:
        # Unified Trading requires category parameter for derivatives
        resp = client.place_active_order(
            category="linear",  # USDT Perpetual
            symbol=symbol,
            side=side,
            order_type="Market",
            qty=qty,
            time_in_force="ImmediateOrCancel",
            reduce_only=reduce_only
        )
    except Exception as e:
        print(f"Order placement exception: {e}")
        resp = {"ret_code": -1, "ret_msg": str(e)}
    send_telegram(f"{side} {symbol} qty={qty} → {resp}")
    return resp

# Flask app
app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
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

    if side == 'buy':
        place_order(symbol, 'Buy', qty)
    elif side == 'sell':
        place_order(symbol, 'Sell', qty)
    elif side == 'exit long':
        place_order(symbol, 'Sell', qty, reduce_only=True)
        bal = get_balance()
        send_telegram(f"Balance after exit long: {bal} {symbol}")
    elif side == 'exit short':
        place_order(symbol, 'Buy', qty, reduce_only=True)
        bal = get_balance()
        send_telegram(f"Balance after exit short: {bal} {symbol}")
    else:
        print("Unknown side:", side)
        return jsonify({"error": "unknown side"}), 400

    return jsonify({"status": "ok"})

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
