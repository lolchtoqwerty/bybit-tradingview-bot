
from flask import Flask, request, jsonify
import os
import requests

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def send_telegram_message(message):
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message
        }
        requests.post(url, json=payload)

@app.route("/", methods=["POST"])
def webhook():
    data = request.json
    print("Received data:", data)
    send_telegram_message(f"🚨 Trade Signal: {data}")
    return jsonify({"status": "success"}), 200
