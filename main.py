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
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET")
if not WEBHOOK_SECRET:
    raise RuntimeError("Missing required environment variable: WEBHOOK_SECRET")

# Allowed trading pairs
ALLOWED_SYMBOLS = {"BTCUSDT", "ETHUSDT", "ADAUSDT", "DOGEUSDT", "PEPEUSDT"}

# Default Buy Percentage: 0.1 %
DEFAULT_BUY_PCT = Decimal("0.001")

# Authenticates the alert request
SECRET_FIELD = "client_secret"



@app.before_request
def log_request_info():
    print(f"[REQUEST] Method:'{request.method}', Path:'{request.path}'")

@app.after_request
def log_response_info(response):
    print(f"[RESPONSE] Method:'{request.method}', Path:'{request.path}' -> Status Code:'{response.status_code}'")
    return response

@app.route('/ping', methods=['GET'])
def ping():
    # print("[PING] Keep-alive ping received.")
    return "pong", 200

@app.route('/', methods=['GET', 'HEAD'])
def root():
    print("[ROOT] Call to root received.")
    return '', 204

@app.route('/health-check', methods=['GET', 'HEAD'])
def health_check():
    print("[HEALTH CHECK] Call to health-check received.")
    return jsonify({"status": "healthy"}), 200

@app.route('/to-the-moon', methods=['POST'])
def webhook():
    print("=====================start=====================")
    data = request.json
    data_for_log = {k: v for k, v in data.items() if k != SECRET_FIELD}
    print(f"[WEBHOOK] Received payload (no {SECRET_FIELD}):", data_for_log)

    # Secret validation
    secret_from_request = data.get(SECRET_FIELD)
    if not secret_from_request or not hmac.compare_digest(secret_from_request, WEBHOOK_SECRET):
        print(f"[SECURITY] Unauthorized access attempt. Invalid or missing {SECRET_FIELD}.")
        return jsonify({"error": "Unauthorized"}), 401

    action = data.get("action", "").strip().upper()
    symbol = data.get("symbol", "BTCUSDT").strip().upper()
    buy_pct_raw = data.get("buy_pct", DEFAULT_BUY_PCT)

    is_buy = action == "BUY"
    is_buy_small_btc = action == "BUY_BTC_SMALL"
    is_sell = action == "SELL"

    # Info log
    info = f"[INFO] Action: {action}, Symbol: {symbol}"
    if is_buy or is_buy_small_btc:
        info += f", Buy %: {buy_pct_raw}"
    print(info)

    # Validate action
    if action not in {"BUY", "BUY_BTC_SMALL", "SELL"}:
        print(f"[ERROR] Invalid action received: {action}")
        return jsonify({"error": "Invalid action"}), 400
    
    # Validate symbol
    if symbol not in ALLOWED_SYMBOLS:
        print(f"[ERROR] Symbol '{symbol}' is not in allowed list.")
        return jsonify({"error": f"Symbol '{symbol}' is not allowed"}), 400

    if is_buy or is_buy_small_btc:
        try:
            buy_pct = Decimal(str(buy_pct_raw))
            if not (Decimal("0") < buy_pct <= Decimal("1")):
                raise ValueError("Out of range")
        except Exception:
            buy_pct = DEFAULT_BUY_PCT
            print(f"[WARNING] Invalid 'buy_pct' provided ({buy_pct_raw}). Defaulting to {DEFAULT_BUY_PCT} (= 0.1 %)")

        try:
            usdt_balance = get_asset_balance("USDT")
            invest_usdt = Decimal(str(usdt_balance)) * buy_pct
            price = Decimal(str(get_current_price(symbol)))
            raw_quantity = (invest_usdt / price).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
            print(f"[INFO] USDT Balance: {usdt_balance:.4f}, Invest {buy_pct*100:.2f}%: {invest_usdt:.4f}")
            print(f"[INFO] {symbol} Price: {price}, Raw quantity before step size rounding: {raw_quantity}")

            # Fetch filters and extract stepSize
            filters = get_symbol_filters(symbol)
            step_size = get_filter_value(filters, "LOT_SIZE", "stepSize")
            print(f"[FILTER] Step size from LOT_SIZE for symbol {symbol}: {step_size}")

            step_sized_quantity = quantize_quantity(invest_usdt / price, step_size)
            print(f"[ORDER] Rounded quantity to conform to LOT_SIZE: {step_sized_quantity}")
        except Exception as e:
            print("[ERROR] Pre-order calculation failed:", str(e))
            return jsonify({"error": f"Buy calculation failed: {str(e)}"}), 500

        try:
            place_binance_order(symbol, "BUY", step_sized_quantity)
            print(f"[ORDER] BUY executed: {step_sized_quantity} {symbol} at {price} on {datetime.now(timezone.utc).isoformat()}")
            response = jsonify({"status": f"Bought {step_sized_quantity} {symbol}"}), 200
            print("[INFO] Buy order completed successfully, returning response:", response)
            print("=====================end=====================")
            return response
        except Exception as e:
            print("[ERROR] Failed to place buy order:", str(e))
            return jsonify({"error": f"Order failed: {str(e)}"}), 500
        
    if is_sell:
        base_asset = symbol.replace("USDT", "")
        try:
            asset_balance = Decimal(str(get_asset_balance(base_asset)))
            if asset_balance > 0:
                # Fetch filters and extract stepSize
                filters = get_symbol_filters(symbol)
                step_size = get_filter_value(filters, "LOT_SIZE", "stepSize")
                print(f"[FILTER] Step size from LOT_SIZE for symbol {symbol}: {step_size}")

                # Round down to conform to Binance stepSize rules
                quantity = quantize_quantity(asset_balance, step_size)
                print(f"[ORDER] Rounded sell quantity to conform to LOT_SIZE: {quantity}")

                if quantity <= Decimal("0"):
                    print("[WARNING] Rounded sell quantity is zero or below minimum tradable size. Aborting.")
                    response = jsonify({"warning": "Sell amount too small after rounding."}), 200
                    print("[INFO] Sell attempt aborted due to to a balance below the minimum size, returning response:", response)
                    print("=====================end=====================")
                    return response

                try:
                    place_binance_order(symbol, "SELL", quantity)
                    price = Decimal(str(get_current_price(symbol)))
                    print(f"[ORDER] SELL executed: {quantity} {symbol} at {price} on {datetime.now(timezone.utc).isoformat()}")
                    response = jsonify({"status": f"Sold {quantity} {symbol}"}), 200
                    print("[INFO] Sell order completed successfully, returning response:", response)
                    print("=====================end=====================")
                    return response
                except Exception as e:
                    print("[ERROR] Failed to place sell order:", str(e))
                    return jsonify({"error": f"Order failed: {str(e)}"}), 500
            else:
                print("[WARNING] No asset balance to sell.")
                response = jsonify({"warning": "No asset to sell"}), 200
                print("[INFO] Sell attempt aborted due to empty balance, returning response:", response)
                print("=====================end=====================")
                return response
        except Exception as e:
            print("[ERROR] Sell pre-check failed:", str(e))
            return jsonify({"error": f"Sell preparation failed: {str(e)}"}), 500

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
    result = response.json()
    print("[BINANCE RESPONSE]", result)

    # Handle Binance API error (in place_binance_order)
    if "code" in result and result["code"] < 0:
        raise Exception(f"[ERROR] Binance API error: {result.get('msg', 'Unknown error')}")

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

        # Handle Binance API error (in get_asset_balace)
        if "code" in result and result["code"] < 0:
            raise Exception(f"[ERROR] Binance API error: {result.get('msg', 'Unknown error')}")

        balances = result.get("balances", [])
        # print_balances(balances)
        for b in balances:
            if b["asset"] == asset:
                print(f"[BALANCE] {asset} free balance: {b['free']}")
                return float(b["free"])
            
        print(f"[WARNING] {asset} balance not found.")
        return 0.0
    
    except Exception as e:
        print(f"[EXCEPTION] Failed to fetch asset balance: {e}")
        return 0.0

def get_symbol_filters(symbol):
    """
    Fetches and prints Binance trading rules (filters) for the given symbol.

    Args:
        symbol (str): Trading pair symbol, e.g., 'BTCUSDT'.
    """
    url = "https://api.binance.com/api/v3/exchangeInfo"
    try:
        response = requests.get(url, params={"symbol": symbol})
        response.raise_for_status()
        data = response.json()

        symbol_info = data.get("symbols", [])[0]
        filters = symbol_info.get("filters", [])
        # print_filters(symbol, filters)
        return filters

    except requests.RequestException as e:
        print(f"[ERROR] Failed to fetch exchange info for {symbol}: {e}")
        return []

def get_current_price(symbol):
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
    response = requests.get(url).json()
    price = float(response["price"])
    print(f"[PRICE] Current price for {symbol}: {price}")
    return price

def quantize_quantity(quantity, step_size):
    step = Decimal(step_size)
    return (Decimal(quantity) // step * step).quantize(step, rounding=ROUND_DOWN)

def get_filter_value(filters, filter_type, key):
    for f in filters:
        if f["filterType"] == filter_type:
            return f.get(key)
    raise ValueError(f"{filter_type} or key '{key}' not found in filters.")

def print_filters(symbol, filters):
    print(f"[INFO] Filters for {symbol}:")
    for f in filters:
        print(f"  - {f['filterType']}: {f}")

def print_balances(balances):
    print("[INFO] Listing all balances returned by Binance with a Total greater than 0:")
    for b in balances:
        current_asset = b["asset"]
        free = float(b.get("free", 0))
        locked = float(b.get("locked", 0))
        total = free + locked
        if total > 0:
            print(f"[BALANCE] {current_asset} - Total: {total}, Free: {free}, Locked: {locked}")

def get_timestamp():
    return int(requests.get("https://api.binance.com/api/v3/time").json()["serverTime"])

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
