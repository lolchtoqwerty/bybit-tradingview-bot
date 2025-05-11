from flask import Flask, request, jsonify
from pybit.unified_trading import HTTP

import os

app = Flask(__name__)

api_key = os.getenv("BYBIT_API_KEY")
api_secret = os.getenv("BYBIT_API_SECRET")
symbol = os.getenv("SYMBOL", "BTCUSDT")
qty = float(os.getenv("POSITION_SIZE", 0.01))

session = HTTP(api_key=api_key, api_secret=api_secret)

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    action = data.get("action", "").upper()

    if action == "LONG":
        order = session.place_order(
            category="linear",
            symbol=symbol,
            side="Buy",
            order_type="Market",
            qty=qty,
            time_in_force="GoodTillCancel"
        )
        return jsonify({"status": "long_order_sent", "response": order})

    elif action == "CLOSE":
        order = session.place_order(
            category="linear",
            symbol=symbol,
            side="Sell",
            order_type="Market",
            qty=qty,
            time_in_force="GoodTillCancel"
        )
        return jsonify({"status": "close_order_sent", "response": order})

    return jsonify({"error": "invalid action"}), 400
