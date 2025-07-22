from flask import Flask, request, jsonify
import hmac, hashlib
import requests
import os
from decimal import Decimal, ROUND_DOWN
from datetime import datetime, timezone

app = Flask(__name__)

# Load API keys from environment variables
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY")
BINANCE_SECRET_KEY = os.environ.get("BINANCE_SECRET_KEY")

# Allowed trading pairs
ALLOWED_SYMBOLS = {"BTCUSDT", "ETHUSDT", "ADAUSDT", "DOGEUSDT", "PEPEUSDT"}

# Default Buy Percentage: 0.1 %
DEFAULT_BUY_PCT = Decimal("0.001")


@app.route('/', methods=['POST'])
def webhook():
    data = request.json
    print("=====================start=====================")
    print("[WEBHOOK] Received payload:", data)

    action = data.get("action", "").strip().upper()
    symbol = data.get("symbol", "BTCUSDT").strip().upper()
    buy_pct_raw = data.get("buy_pct", DEFAULT_BUY_PCT)

    is_buy = action == "BUY"

    # Info log
    info = f"[INFO] Action: {action}, Symbol: {symbol}"
    if is_buy:
        info += f", Buy %: {buy_pct_raw}"
    print(info)

    # Validate action
    if action not in {"BUY", "SELL"}:
        print(f"[ERROR] Invalid action received: {action}")
        return jsonify({"error": "Invalid action"}), 400
    
    # Validate symbol
    if symbol not in ALLOWED_SYMBOLS:
        print(f"[ERROR] Symbol '{symbol}' is not in allowed list.")
        return jsonify({"error": f"Symbol '{symbol}' is not allowed"}), 400

    if is_buy:
        try:
            buy_pct = Decimal(str(buy_pct_raw))
            if not (Decimal("0") < buy_pct <= Decimal("1")):
                raise ValueError("Out of range")
        except Exception:
            buy_pct = DEFAULT_BUY_PCT
            print(f"[WARNING] Invalid 'buy_pct' provided ({buy_pct_raw}). Defaulting to {DEFAULT_BUY_PCT} (= 0.1 %)")

        usdt_balance = get_asset_balance("USDT")
        invest_usdt = Decimal(str(usdt_balance)) * buy_pct
        price = Decimal(str(get_current_price(symbol)))
        quantity = (invest_usdt / price).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)

        print(f"[INFO] USDT Balance: {usdt_balance:.4f}, Invest {buy_pct*100:.2f}%: {invest_usdt:.4f}")
        print(f"[INFO] {symbol} Price: {price}, Quantity to BUY: {quantity}")

        place_binance_order(symbol, "BUY", quantity)
        print(f"[ORDER] BUY executed: {quantity} {symbol} at {price} on {datetime.now(timezone.utc).isoformat()}")
        response = jsonify({"status": f"Bought {quantity} {symbol}"}), 200
        print("[INFO] Buy order completed successfully, returning response:", response)
        print("=====================end=====================")
        return response        

    else:
        base_asset = symbol.replace("USDT", "")
        asset_balance = Decimal(str(get_asset_balance(base_asset)))
        if asset_balance > 0:
            quantity = asset_balance.quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
            place_binance_order(symbol, "SELL", quantity)
            price = Decimal(str(get_current_price(symbol)))
            print(f"[ORDER] SELL executed: {quantity} {symbol} at {price} on {datetime.now(timezone.utc).isoformat()}")
            response = jsonify({"status": f"Sold {quantity} {symbol}"}), 200
            print("[INFO] Sell order completed successfully, returning response:", response)
            print("=====================end=====================")
            return response
        else:
            print("[WARNING] No asset balance to sell.")
            response = jsonify({"warning": "No asset to sell"}), 200
            print("[INFO] Sell attempt aborted due to empty balance, returning response:", response)
            print("=====================end=====================")
            return response

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
    try:
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

        # Handle Binance API errors
        if "code" in result and result["code"] < 0:
            print("---")
            print("print result code:")
            print(result["code"])
            print("print result:")
            print(result)
            print("---")
            print(f"[ERROR] Binance API error: {result.get('msg', 'Unknown error')}")
            return 0.0

        balances = result.get("balances", [])

        print("[DEBUG] START")
        print("[DEBUG] Listing all balances returned by Binance:")
        for b in balances:
            print(f"  - {b['asset']}: {b['free']} (free), {b['locked']} (locked)")
        print("[DEBUG] FIN")

        for b in balances:
            if b["asset"] == asset:
                print(f"[BALANCE] {asset} balance: {b['free']}")
                return float(b["free"])
            
        print(f"[WARNING] {asset} balance not found.")
        return 0.0
    
    except Exception as e:
        print(f"[EXCEPTION] Failed to fetch asset balance: {e}")
        return 0.0    

def get_current_price(symbol):
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
    response = requests.get(url).json()
    price = float(response["price"])
    print(f"[PRICE] Current price for {symbol}: {price}")
    return price

def get_timestamp():
    return int(requests.get("https://api.binance.com/api/v3/time").json()["serverTime"] / 1000)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
