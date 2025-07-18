from flask import Flask, request, jsonify
import hmac, hashlib
import requests
import os

app = Flask(__name__)

BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY")
BINANCE_SECRET_KEY = os.environ.get("BINANCE_SECRET_KEY")

@app.route('/', methods=['POST'])
def webhook():
    data = request.json
    print("Webhook received:", data)

    action = data.get("action", "").upper()  # BUY or SELL
    symbol = data.get("symbol", "BTCUSDT").upper()
    quantity = float(data.get("size", 0.001))

    if action not in ["BUY", "SELL"]:
        return jsonify({"error": "Invalid action"}), 400

    place_binance_order(symbol, action, quantity)
    return jsonify({"status": "Order sent"}), 200

def place_binance_order(symbol, side, quantity):
    url = "https://api.binance.com/api/v3/order"
    params = {
        "symbol": symbol,
        "side": side,
        "type": "MARKET",
        "quantity": quantity,
        "timestamp": get_timestamp()
    }
    headers = {
        "X-MBX-APIKEY": BINANCE_API_KEY
    }
    query_string = '&'.join([f"{k}={v}" for k, v in params.items()])
    signature = hmac.new(BINANCE_SECRET_KEY.encode(), query_string.encode(), hashlib.sha256).hexdigest()
    params["signature"] = signature
    response = requests.post(url, headers=headers, params=params)
    print("Binance response:", response.json())

def get_timestamp():
    return int(requests.get("https://api.binance.com/api/v3/time").json()["serverTime"])

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
