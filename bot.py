import os
import time
import math
import hmac
import hashlib
import json
from flask import Flask, request, jsonify
import requests

# Load config from env
API_KEY = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")
BASE_URL = os.getenv("BYBIT_BASE_URL", "https://api-testnet.bybit.com")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

app = Flask(__name__)

def send_telegram(message: str):
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message}
        )

def sign_v5(ts, recv_window, body):
    return hmac.new(
        API_SECRET.encode(), 
        (ts + API_KEY + recv_window + body).encode(),
        hashlib.sha256
    ).hexdigest()

def get_balance():
    path, ts, recv = "/v5/account/wallet-balance", str(int(time.time()*1000)), "5000"
    sign = sign_v5(ts, recv, "")
    headers = {"X-BAPI-API-KEY": API_KEY, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": recv, "X-BAPI-SIGN": sign}
    res = requests.get(BASE_URL+path+"?category=linear&coin=USDT", headers=headers).json()
    return res.get('result', {}).get('USDT', {}).get('equity')

def get_mark_price(sym):
    path, ts, recv = "/v5/market/tickers", str(int(time.time()*1000)), "5000"
    sign = sign_v5(ts, recv, "")
    hdr = {"X-BAPI-API-KEY": API_KEY, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": recv, "X-BAPI-SIGN": sign}
    j = requests.get(f"{BASE_URL}{path}?category=linear&symbol={sym}", headers=hdr).json()
    lst = j.get('result', {}).get('list', [])
    return float(lst[0]['lastPrice']) if lst else None

def place_order(symbol, side, qty, reduce_only=False):
    price = get_mark_price(symbol)
    if price:
        minq = math.ceil(5/price)
        qty = minq if qty<minq else qty
    path, ts, recv = "/v5/order/create", str(int(time.time()*1000)), "5000"
    payload = json.dumps({"category":"linear","symbol":symbol,"side":side.capitalize(),"orderType":"Market","qty":str(qty),"timeInForce":"ImmediateOrCancel","reduceOnly":reduce_only})
    sign = sign_v5(ts, recv, payload)
    hdr = {"Content-Type":"application/json","X-BAPI-API-KEY":API_KEY,"X-BAPI-TIMESTAMP":ts,"X-BAPI-RECV-WINDOW":recv,"X-BAPI-SIGN":sign}
    res = requests.post(BASE_URL+path, headers=hdr, data=payload).json()
    send_telegram(f"{side} {symbol} qty={qty} → {res}")
    return res

@app.route('/webhook', methods=['POST'])
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json(force=True)
    sym, sd, qt = data['symbol'], data['side'], float(data.get('qty',1))
    # Получаем текущую позицию
    def get_position(symbol: str):
        path = "/v5/position/list"
        ts = str(int(time.time() * 1000))
        recv_window = "5000"
        sign = sign_v5(ts, recv_window, "")
        headers = {
            "X-BAPI-API-KEY": API_KEY,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": recv_window,
            "X-BAPI-SIGN": sign,
        }
        url = BASE_URL + path + f"?category=linear&symbol={symbol}"
        r = requests.get(url, headers=headers).json()
        lst = r.get('result', {}).get('list', [])
        return lst[0] if lst else None

    pos = get_position(sym)
    size = float(pos.get('size', 0)) if pos else 0

    if sd == 'buy':
        place_order(sym,'Buy',qt)
    elif sd == 'sell':
        place_order(sym,'Sell',qt)
    elif sd == 'exit long':
        # закрыть лонг только если есть положительная позиция
        if size > 0:
            place_order(sym,'Sell',qt,True)
        send_telegram(f"Баланс после лонга: {get_balance()} USDT")
    elif sd == 'exit short':
        # закрыть шорт только если есть отрицательная позиция
        if size < 0:
            place_order(sym,'Buy',qt,True)
        send_telegram(f"Баланс после шорта: {get_balance()} USDT")
    else:
        return jsonify(error="unknown side"),400
    return jsonify(status="ok")
    data = request.get_json(force=True)
    sym, sd, qt = data['symbol'], data['side'], float(data.get('qty',1))
    if sd=='buy': place_order(sym,'Buy',qt)
    elif sd=='sell': place_order(sym,'Sell',qt)
    elif sd=='exit long': place_order(sym,'Sell',qt,True); send_telegram(f"Баланс после лонга: {get_balance()} USDT")
    elif sd=='exit short': place_order(sym,'Buy',qt,True); send_telegram(f"Баланс после шорта: {get_balance()} USDT")
    else: return jsonify(error="unknown side"),400
    return jsonify(status="ok")

if __name__=='__main__': app.run(host='0.0.0.0', port=int(os.getenv('PORT',5000)))
