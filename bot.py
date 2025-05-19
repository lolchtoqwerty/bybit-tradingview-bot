import os
import time
import hmac
import hashlib
from flask import Flask, request, jsonify
import requests

# Load configuration from environment
API_KEY    = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")
BASE_URL   = os.getenv("BYBIT_BASE_URL", "https://api-testnet.bybit.com")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Helper: send notification to Telegram (optional)
def send_telegram(message: str):
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
        try:
            requests.post(url, json=payload)
        except Exception as e:
            print(f"Telegram error: {e}")

# Place a market order on Bybit Testnet
def place_order(symbol: str, side: str, qty: float):
    path   = "/v2/private/order/create"
    ts     = int(time.time() * 1000)
    params = {
        "api_key": API_KEY,
        "symbol": symbol,
        "side": side.upper(),    # BUY or SELL
        "order_type": "Market",
        "qty": qty,
        "time_in_force": "GoodTillCancel",
        "timestamp": ts
    }
    # Create signature
    sorted_params = '&'.join(f"{k}={v}" for k, v in sorted(params.items()))
    params['sign'] = hmac.new(
        API_SECRET.encode(), sorted_params.encode(), hashlib.sha256
    ).hexdigest()

    url = BASE_URL + path
    resp = requests.post(url, params=params, timeout=10)
    result = resp.json()
    send_telegram(f"Order {side}: {symbol} qty={qty} -> {result}")
    return result

# Flask app
app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json(force=True)
    # Expected fields: symbol, side, leverage (optional), timeframe
    symbol = data.get('symbol')
    side   = data.get('side')
    leverage = data.get('leverage', 1)

    # Determine quantity (fixed or based on leverage)
    qty = data.get('qty', 1)
    # Override qty logic as needed

    # Execute order
    if side in ('buy', 'sell'):
        resp = place_order(symbol, 'Buy' if side=='buy' else 'Sell', qty)
        return jsonify(resp)
    elif side.startswith('exit'):
        # For exits, just print/log
        send_telegram(f"Exit signal: {data}")
        return jsonify({"status": "exit", "data": data})
    else:
        return jsonify({"error": "unknown side"}), 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)))
