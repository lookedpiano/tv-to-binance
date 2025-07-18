from flask import Flask, request, jsonify
import hmac, hashlib
import requests
import os

app = Flask(__name__)

# Load API keys from Render environment variables
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY")
BINANCE_SECRET_KEY = os.environ.get("BINANCE_SECRET_KEY")

# In-memory tracker of currently held position size
open_position_size = 0.0

@app.route('/', methods=['POST'])
def webhook():
    global open_position_size

    data = request.json
    print("Webhook received:", data)

    action = data.get("action", "").upper()  # Expected: "BUY" or "SELL"
    symbol = data.get("symbol", "BTCUSDT").upper()

    if action not in ["BUY", "SELL"]:
        return jsonify({"error": "Invalid action"}), 400

    if action == "BUY":
        # Calculate 1‰ (0.1%) of available USDT
        usdt_balance = get_asset_balance("USDT")
        invest_usdt = usdt_balance * 0.001
        price = get_current_price(symbol)
        quantity = round(invest_usdt / price, 6)

        # Place market buy
        place_binance_order(symbol, "BUY", quantity)
        open_position_size = quantity  # Save the position
        print(f"BUY executed: {quantity} {symbol}")
        return jsonify({"status": f"Bought {quantity} {symbol}"}), 200

    elif action == "SELL":
        if open_position_size > 0:
            place_binance_order(symbol, "SELL", open_position_size)
            print(f"SELL executed: {open_position_size} {symbol}")
            open_position_size = 0  # Reset position tracker
            return jsonify({"status": f"Sold {symbol}"}), 200
        else:
            print("No open position to sell.")
            return jsonify({"warning": "No position to close"}), 200

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

def get_asset_balance(asset):
    url = "https://api.binance.com/api/v3/account"
    timestamp = get_timestamp()
    query_string = f"timestamp={timestamp}"
    signature = hmac.new(BINANCE_SECRET_KEY.encode(), query_string.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-MBX-APIKEY": BINANCE_API_KEY
    }
    response = requests.get(f"{url}?{query_string}&signature={signature}", headers=headers)
    balances = response.json().get("balances", [])
    for b in balances:
        if b["asset"] == asset:
            return float(b["free"])
    return 0.0

def get_current_price(symbol):
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
    return float(requests.get(url).json()["price"])

def get_timestamp():
    return int(requests.get("https://api.binance.com/api/v3/time").json()["serverTime"])

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
