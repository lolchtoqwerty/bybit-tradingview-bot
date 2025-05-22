import os
import time
import hmac
import hashlib
import json
import math
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

# Получение цены инструмента для расчёта минимального объёма
def get_mark_price(symbol: str):
    path = "/v5/market/tick"
    ts = str(int(time.time() * 1000))
    recv_window = "5000"
    body = ""
    sign = sign_v5(ts, recv_window, body)
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv_window,
        "X-BAPI-SIGN": sign,
    }
    url = BASE_URL + path + f"?symbol={symbol}"
    r = requests.get(url, headers=headers)
    try:
        data = r.json().get('result', {}).get('list', [])[0]
        return float(data.get('lastPrice', 0))
    except Exception as e:
        print(f"Ошибка получения цены: {e}")
        return None

# Размещение рыночного ордера через v5 API
def place_order(symbol: str, side: str, qty: float, reduce_only: bool=False):
    # Проверяем минимум (5 USDT)
    price = get_mark_price(symbol)
    if price:
        min_qty = math.ceil(5 / price)
        if qty < min_qty:
            print(f"Qty {qty} ниже минимума, меняем на {min_qty}")
