import os
import time
import hmac
import hashlib
import json
from flask import Flask, request, jsonify
import requests

# Загружаем конфигурацию из переменных окружения
API_KEY    = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")
BASE_URL   = os.getenv("BYBIT_BASE_URL", "https://api-testnet.bybit.com")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Функция отправки сообщений в Telegram
def send_telegram(message: str):
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
        try:
            requests.post(url, json=payload)
        except Exception as e:
            print(f"Ошибка Telegram: {e}")

# Функция для подписи запросов v5 API
def sign_v5(ts: str, recv_window: str, body: str) -> str:
    to_sign = ts + API_KEY + recv_window + body
    return hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()

# Получение баланса кошелька (USDT)
def get_balance(currency="USDT"):
    path = "/v5/account/wallet-balance"
    ts = str(int(time.time() * 1000))
    recv_window = "5000"
    query = f"?category=linear&coin={currency}"
    body = ""
    sign = sign_v5(ts, recv_window, body)
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv_window,
        "X-BAPI-SIGN": sign,
    }
    url = BASE_URL + path + query
    response = requests.get(url, headers=headers)
    try:
        result = response.json()
        return result.get('result', {}).get(currency, {}).get('equity')
    except Exception as e:
        print(f"Ошибка получения баланса: {e}, код={response.status_code}, тело={response.text}")
        return None

# Размещение рыночного ордера через v5 API
def place_order(symbol: str, side: str, qty: float, reduce_only: bool=False):
    print("=== Вызов ордера ===", symbol, side, qty, "reduce_only=", reduce_only)
    path = "/v5/order/create"
    ts = str(int(time.time() * 1000))
    recv_window = "5000"
    payload = {
        "category": "linear",
        "symbol": symbol,
        "side": side.capitalize(),
        "orderType": "Market",
        "qty": str(qty),
        "timeInForce": "ImmediateOrCancel",
        "reduceOnly": reduce_only,
    }
    body = json.dumps(payload)
    sign = sign_v5(ts, recv_window, body)
    headers = {
        "Content-Type": "application/json",
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv_window,
        "X-BAPI-SIGN": sign,
    }
    url = BASE_URL + path
    response = requests.post(url, headers=headers, data=body)
    try:
        result = response.json()
    except Exception:
        result = {"ret_code": -1, "ret_msg": response.text}
    send_telegram(f"{side} {symbol} qty={qty} → {result}")
    return result

# Инициализация Flask приложения
app = Flask(__name__)

# Обработчик вебхука от TradingView
@app.route('/webhook', methods=['POST'])
def webhook():
    print("=== Получен вебхук ===", request.data)
    try:
        data = request.get_json(force=True)
        print("Разобранный JSON:", data)
    except Exception as e:
        print(f"Ошибка парсинга JSON: {e}")
        return jsonify({"error": "invalid JSON"}), 400

    symbol = data.get('symbol')
    side   = data.get('side')
    qty    = float(data.get('qty', 1))

    if side == 'buy':
        place_order(symbol, 'Buy', qty)
    elif side == 'sell':
        place_order(symbol, 'Sell', qty)
    elif side == 'exit long':
        place_order(symbol, 'Sell', qty, reduce_only=True)
        bal = get_balance()
        send_telegram(f"Баланс после закрытия лонга: {bal} USDT")
    elif side == 'exit short':
        place_order(symbol, 'Buy', qty, reduce_only=True)
        bal = get_balance()
        send_telegram(f"Баланс после закрытия шорта: {bal} USDT")
    else:
        print(f"Неизвестный side: {side}")
        return jsonify({"error": "unknown side"}), 400

    return jsonify({"status": "ok"})

# Запуск приложения
if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
