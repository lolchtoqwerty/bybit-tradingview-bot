from flask import Flask, request, jsonify
import hmac
import hashlib
import time
import requests
import os
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_SECRET = os.getenv("BYBIT_SECRET")
BASE_URL = "https://api.bybit.com"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

DEFAULT_SYMBOL = "SUSDT"
DEFAULT_LEVERAGE = 3

def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
        requests.post(url, json=payload)
    except Exception as e:
        print("Telegram error:", e)

def bybit_request(method, endpoint, payload=None):
    if payload is None:
        payload = {}

    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    params = {"api_key": BYBIT_API_KEY, "timestamp": timestamp, "recv_window": recv_window, **payload}
    ordered_params = dict(sorted(params.items()))
    query_string = "&".join([f"{k}={v}" for k, v in ordered_params.items()])
    signature = hmac.new(BYBIT_SECRET.encode(), query_string.encode(), hashlib.sha256).hexdigest()
    params["sign"] = signature
    url = f"{BASE_URL}{endpoint}"

    if method == "POST":
        response = requests.post(url, data=params)
    else:
        response = requests.get(url, params=params)
    return response.json()

def place_order(symbol, side, leverage):
    bybit_request("POST", "/v2/private/position/leverage/save", {
        "symbol": symbol,
        "leverage": leverage
    })
    result = bybit_request("POST", "/v2/private/order/create", {
        "symbol": symbol,
        "side": side.upper(),
        "order_type": "Market",
        "qty": 5,
        "time_in_force": "GoodTillCancel",
        "reduce_only": False
    })
    send_telegram(f"🚀 OPEN {side.upper()} {symbol} | Leverage: {leverage}")
    return result

def close_position(symbol, side):
    opposite = "Sell" if side == "buy" else "Buy"
    result = bybit_request("POST", "/v2/private/order/create", {
        "symbol": symbol,
        "side": opposite,
        "order_type": "Market",
        "qty": 5,
        "reduce_only": True,
        "time_in_force": "GoodTillCancel"
    })
    send_telegram(f"❌ CLOSE {side.upper()} {symbol}")
    return result

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    print("Received:", data)

    symbol = data.get("symbol", DEFAULT_SYMBOL)
    leverage = data.get("leverage", DEFAULT_LEVERAGE)
    side = data.get("side", "")

    if side == "buy" or side == "sell":
        response = place_order(symbol, side, leverage)
    elif side == "exit long":
        response = close_position(symbol, "buy")
    elif side == "exit short":
        response = close_position(symbol, "sell")
    else:
        return jsonify({"error": "Invalid side"}), 400

    return jsonify(response)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)