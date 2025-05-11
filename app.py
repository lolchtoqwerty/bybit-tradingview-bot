from flask import Flask, request, jsonify
from pybit.unified_trading import HTTP
import os

app = Flask(__name__)

# Получаем переменные среды
api_key = os.getenv("BYBIT_API_KEY")
api_secret = os.getenv("BYBIT_API_SECRET")
symbol = os.getenv("SYMBOL", "BTCUSDT")

# Подключаемся к Bybit через pybit Unified HTTP API
session = HTTP(api_key=api_key, api_secret=api_secret)

def get_balance(asset="USDT"):
    balance_data = session.get_wallet_balance(accountType="UNIFIED")  # UNIFIED для USDT фьючерсов
    usdt_data = balance_data.get("result", {}).get("list", [])[0]
    coin_balances = usdt_data.get("coin", [])
    for coin in coin_balances:
        if coin.get("coin") == asset:
            return float(coin.get("availableToTrade", 0))
    return 0.0

def get_price():
    ticker = session.get_ticker(category="linear", symbol=symbol)
    return float(ticker["result"]["list"][0]["lastPrice"])

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    action = data.get("action", "").upper()

    try:
        balance = get_balance("USDT")
        price = get_price()
        qty = round(balance / price, 3)  # округляем до 0.001 BTC

        if action == "LONG":
            order = session.place_order(
                category="linear",
                symbol=symbol,
                side="Buy",
                order_type="Market",
                qty=qty,
                time_in_force="GoodTillCancel"
            )
            return jsonify({"status": "long_order_sent", "qty": qty, "order": order})

        elif action == "CLOSE":
            order = session.place_order(
                category="linear",
                symbol=symbol,
                side="Sell",
                order_type="Market",
                qty=qty,
                time_in_force="GoodTillCancel"
            )
            return jsonify({"status": "close_order_sent", "qty": qty, "order": order})

        return jsonify({"error": "invalid action"}), 400

    except Exception as e:
        return jsonify({"error": str(e)}), 500
