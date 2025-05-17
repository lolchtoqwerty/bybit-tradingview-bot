
import os
import json
import requests
from flask import Flask, request, jsonify
from pybit.unified_trading import HTTP

app = Flask(__name__)

api_key = os.getenv("BYBIT_API_KEY")
api_secret = os.getenv("BYBIT_API_SECRET")
telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")

session = HTTP(api_key=api_key, api_secret=api_secret)

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
    payload = {"chat_id": telegram_chat_id, "text": message}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Telegram error: {e}")

@app.route("/", methods=["POST"])
def webhook():
    data = request.json
    try:
        symbol = data["symbol"]
        tf = data["timeframe"]
        side = data["side"]
        leverage = int(data["leverage"])

        # Установка изолированной маржи и плеча
        session.set_margin_mode(category="linear", symbol=symbol, tradeMode=1)
        session.set_leverage(category="linear", symbol=symbol, buyLeverage=leverage, sellLeverage=leverage)

        # Получение баланса
        wallet = session.get_wallet_balance(accountType="UNIFIED")
        available = float(wallet["result"]["list"][0]["coin"][0]["availableToTrade"])
        usdt_amount = available * 0.20

        # Получение текущей цены
        ob = session.get_orderbook(symbol=symbol)
        price = float(ob["result"]["b"][0][0])

        # Расчет объема
        qty = round(usdt_amount / price, 3)

        if side in ["buy", "sell"]:
            session.place_order(
                category="linear",
                symbol=symbol,
                side="Buy" if side == "buy" else "Sell",
                orderType="Market",
                qty=qty,
                timeInForce="GoodTillCancel"
            )
            send_telegram_message(f"✅ Открыт {'лонг' if side == 'buy' else 'шорт'} по {symbol} на {qty} (20% баланса)")
        elif side == "exit long":
            session.place_order(
                category="linear",
                symbol=symbol,
                side="Sell",
                orderType="Market",
                qty=qty,
                timeInForce="GoodTillCancel",
                reduceOnly=True
            )
            send_telegram_message(f"🔻 Закрыт лонг по {symbol}")
        elif side == "exit short":
            session.place_order(
                category="linear",
                symbol=symbol,
                side="Buy",
                orderType="Market",
                qty=qty,
                timeInForce="GoodTillCancel",
                reduceOnly=True
            )
            send_telegram_message(f"🔺 Закрыт шорт по {symbol}")
        else:
            return jsonify({"error": "Unknown signal"}), 400

        return jsonify({"status": "order placed"}), 200
    except Exception as e:
        send_telegram_message(f"❌ Ошибка: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
