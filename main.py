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
    print(f"[STATE] Current open_position_size before processing: {open_position_size}")

    data = request.json
    print("[WEBHOOK] Received payload:", data)

    action = data.get("action", "").upper() # Expected: "BUY" or "SELL"
    symbol = data.get("symbol", "BTCUSDT").upper()

    print(f"[INFO] Action: {action}, Symbol: {symbol}")

    if action not in ["BUY", "SELL"]:
        print("[ERROR] Invalid action received:", action)
        return jsonify({"error": "Invalid action"}), 400

    if action == "BUY":
        # Calculate 1‰ (0.1%) of available USDT
        usdt_balance = get_asset_balance("USDT")
        invest_usdt = usdt_balance * 0.001
        price = get_current_price(symbol)
        quantity = round(invest_usdt / price, 6)

        # Place market buy
        print(f"[INFO] USDT Balance: {usdt_balance:.4f}, Invest 0.1% (1‰): {invest_usdt:.4f}")
        print(f"[INFO] {symbol} Price: {price:.4f}, Calculated Quantity to BUY: {quantity:.6f}")

        place_binance_order(symbol, "BUY", quantity)
        open_position_size = quantity # Save the position
        print(f"[ORDER] BUY executed: {quantity} {symbol}")
        return jsonify({"status": f"Bought {quantity} {symbol}"}), 200

    elif action == "SELL":
        if open_position_size > 0:
            print(f"[INFO] Selling open position: {open_position_size} {symbol}")
            place_binance_order(symbol, "SELL", open_position_size)
            print(f"[ORDER] SELL executed: {open_position_size} {symbol}")
            open_position_size = 0.0 # Reset position tracker
            return jsonify({"status": f"Sold {symbol}"}), 200
        else:
            print("[WARNING] No open position to sell.")
            return jsonify({"warning": "No position to close"}), 200

@app.route('/ping', methods=['GET'])
def ping():
    print("[PING] Received keep-alive ping.")
    return "pong", 200

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
    print(f"[REQUEST] Sending {side} order to Binance for {symbol}, Quantity: {quantity}")
    response = requests.post(url, headers=headers, params=params)
    print("[BINANCE RESPONSE]", response.json())

def get_asset_balance(asset):
    url = "https://api.binance.com/api/v3/account"
    timestamp = get_timestamp()
    query_string = f"timestamp={timestamp}"
    signature = hmac.new(BINANCE_SECRET_KEY.encode(), query_string.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-MBX-APIKEY": BINANCE_API_KEY
    }
    full_url = f"{url}?{query_string}&signature={signature}"
    response = requests.get(full_url, headers=headers)
    result = response.json()
    balances = result.get("balances", [])
    for b in balances:
        if b["asset"] == asset:
            print(f"[BALANCE] {asset} balance: {b['free']}")
            return float(b["free"])
    print(f"[WARNING] {asset} balance not found.")
    return 0.0

def get_current_price(symbol):
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
    response = requests.get(url).json()
    price = float(response["price"])
    print(f"[PRICE] Current price for {symbol}: {price}")
    return price

def get_timestamp():
    return int(requests.get("https://api.binance.com/api/v3/time").json()["serverTime"])

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
