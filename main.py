from flask import Flask, request, jsonify
import hmac, hashlib
import requests
import os, json
from decimal import Decimal, ROUND_DOWN
from datetime import datetime, timezone

app = Flask(__name__)

# Load API keys from environment variables
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY")
BINANCE_SECRET_KEY = os.environ.get("BINANCE_SECRET_KEY")

# Allowed trading pairs
ALLOWED_SYMBOLS = {"BTCUSDT", "ETHUSDT", "ADAUSDT", "DOGEUSDT"}

POSITIONS_DIR = "positions"
os.makedirs(POSITIONS_DIR, exist_ok=True)

@app.route('/', methods=['POST'])
def webhook():
    data = request.json
    print("[WEBHOOK] Received payload:", data)

    action = data.get("action", "").upper()
    symbol = data.get("symbol", "BTCUSDT").upper()

    print(f"[INFO] Action: {action}, Symbol: {symbol}")

    if action not in ["BUY", "SELL"]:
        print("[ERROR] Invalid action received:", action)
        return jsonify({"error": "Invalid action"}), 400
    
    if symbol not in ALLOWED_SYMBOLS:
        print(f"[ERROR] Symbol '{symbol}' is not in allowed list.")
        return jsonify({"error": f"Symbol '{symbol}' is not allowed"}), 400

    if action == "BUY":
        usdt_balance = get_asset_balance("USDT")
        invest_usdt = Decimal(str(usdt_balance)) * Decimal("0.001")  # 0.1%
        price = Decimal(str(get_current_price(symbol)))
        quantity = (invest_usdt / price).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)

        print(f"[INFO] USDT Balance: {usdt_balance:.4f}, Invest 0.1%: {invest_usdt:.4f}")
        print(f"[INFO] {symbol} Price: {price}, Quantity to BUY: {quantity}")

        place_binance_order(symbol, "BUY", quantity)
        write_position(symbol, quantity, price)
        print(f"[ORDER] BUY executed: {quantity} {symbol}")
        return jsonify({"status": f"Bought {quantity} {symbol}"}), 200

    elif action == "SELL":
        position = read_position(symbol)
        if position:
            quantity = Decimal(position["quantity"])
            print(f"[INFO] Selling quantity: {quantity} {symbol}")
            place_binance_order(symbol, "SELL", quantity)
            delete_position(symbol)
            print(f"[ORDER] SELL executed: {quantity} {symbol}")
            return jsonify({"status": f"Sold {symbol}"}), 200
        else:
            print("[WARNING] No open position to sell.")
            return jsonify({"warning": "No position to close"}), 200

@app.route('/ping', methods=['GET'])
def ping():
    print("[PING] Keep-alive ping received.")
    return "pong", 200

def place_binance_order(symbol, side, quantity):
    url = "https://api.binance.com/api/v3/order"
    params = {
        "symbol": symbol,
        "side": side,
        "type": "MARKET",
        "quantity": str(quantity),
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
    # Binance serverTime is in milliseconds, convert to seconds for UNIX timestamp
    return int(requests.get("https://api.binance.com/api/v3/time").json()["serverTime"] / 1000)

# --- Position file helpers ---
def position_filepath(symbol):
    return os.path.join(POSITIONS_DIR, f"{symbol}.json")

def write_position(symbol, quantity, buy_price):
    timestamp = get_timestamp()
    data = {
        "quantity": str(quantity),
        "buy_price": str(buy_price),
        "timestamp": timestamp,
        "timestamp_human": datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
    }
    with open(position_filepath(symbol), "w") as f:
        json.dump(data, f, indent=2)
    print(f"[FILE] Position saved to {position_filepath(symbol)}")

def read_position(symbol):
    path = position_filepath(symbol)
    if os.path.exists(path):
        with open(path, "r") as f:
            data = json.load(f)
            print(f"[FILE] Read position file: {data}")
            return data
    else:
        print(f"[FILE] No position file for {symbol}")
        return None

def delete_position(symbol):
    path = position_filepath(symbol)
    if os.path.exists(path):
        os.remove(path)
        print(f"[FILE] Deleted position file: {path}")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
