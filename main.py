from flask import Flask, request, jsonify
import hmac, hashlib
import requests
import os
import logging
from decimal import Decimal, ROUND_DOWN
from datetime import datetime, timezone

# -------------------------
# Logging configuration
# -------------------------
logging.basicConfig(
    level=logging.INFO,
    format='[%(levelname)s] %(message)s'
)

app = Flask(__name__)

# -------------------------
# Environment variables
# -------------------------
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY")
BINANCE_SECRET_KEY = os.environ.get("BINANCE_SECRET_KEY")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET")
PORT = os.environ.get("PORT")

if not BINANCE_API_KEY:
    raise RuntimeError("Missing required environment variable: BINANCE_API_KEY")
if not BINANCE_SECRET_KEY:
    raise RuntimeError("Missing required environment variable: BINANCE_SECRET_KEY")
if not WEBHOOK_SECRET:
    raise RuntimeError("Missing required environment variable: WEBHOOK_SECRET")
if not PORT:
    raise RuntimeError(
        "Missing required environment variable: PORT.\n"
        "The following ports are reserved by Render and cannot be used: 18012, 18013 and 19099.\n"
        "Choose a port such that: 1024 < PORT <= 49000, excluding the reserved ones."
    )


# -------------------------
# Configuration
# -------------------------
ALLOWED_SYMBOLS = {"BTCUSDT", "ETHUSDT", "ADAUSDT", "DOGEUSDT", "PEPEUSDT", "XRPUSDT"}
DEFAULT_BUY_PCT = Decimal("0.001") # 0.1 %
SECRET_FIELD = "client_secret"


# -------------------------
# Utilities
# -------------------------
def should_log_request():
    return request.path not in ('/health-check', '/healthz', '/ping', '/')

def get_timestamp_ms():
    """Return Binance serverTime in milliseconds (int)."""
    return int(requests.get("https://api.binance.com/api/v3/time").json()["serverTime"])

def sign_query(params: dict):
    """
    Build query string and signature for Binance signed endpoints.
    Uses timestamp in ms if caller didn't pass it.
    """
    if "timestamp" not in params:
        params["timestamp"] = get_timestamp_ms()
    # Build query in insertion order (consistent)
    qs = '&'.join([f"{k}={params[k]}" for k in params])
    signature = hmac.new(BINANCE_SECRET_KEY.encode(), qs.encode(), hashlib.sha256).hexdigest()
    return qs + "&signature=" + signature

def quantize_quantity(quantity: Decimal, step_size_str: str) -> Decimal:
    """Round down quantity to conform to stepSize."""
    step = Decimal(step_size_str)
    # floor to step multiple
    quant = (Decimal(quantity) // step) * step
    # quantize to the same scale as step
    return quant.quantize(step, rounding=ROUND_DOWN)

def get_filter_value(filters, filter_type, key):
    for f in filters:
        if f.get("filterType") == filter_type:
            return f.get(key)
    raise ValueError(f"{filter_type} or key '{key}' not found in filters.")

# -------------------------
# Binance helper functions
# -------------------------
def signed_get(path: str, params: dict = None):
    if params is None:
        params = {}
    qs_sig = sign_query(params)
    url = f"https://api.binance.com{path}?{qs_sig}"
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    r = requests.get(url, headers=headers)
    try:
        res = r.json()
    except Exception:
        r.raise_for_status()
    if isinstance(res, dict) and res.get("code", 0) < 0:
        raise Exception(f"Binance API error: {res.get('msg')}")
    return res

def signed_post(path: str, params: dict = None):
    if params is None:
        params = {}
    qs_sig = sign_query(params)
    url = f"https://api.binance.com{path}?{qs_sig}"
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    r = requests.post(url, headers=headers)
    try:
        res = r.json()
    except Exception:
        r.raise_for_status()
    if isinstance(res, dict) and res.get("code", 0) < 0:
        raise Exception(f"Binance API error: {res.get('msg')}")
    return res

def public_get(path: str, params: dict = None):
    url = f"https://api.binance.com{path}"
    r = requests.get(url, params=params)
    r.raise_for_status()
    return r.json()

# -------------------------
# Exchange helpers
# -------------------------
def get_symbol_filters(symbol):
    try:
        data = public_get("/api/v3/exchangeInfo", {"symbol": symbol})
        symbols = data.get("symbols", [])
        if not symbols:
            return []
        filters = symbols[0].get("filters", [])
        # log_filters(symbol, filters)
        return filters
    except Exception as e:
        logging.exception(f"Failed to fetch exchangeInfo for {symbol}: {e}")
        return []
    
def get_current_price(symbol):
    try:
        data = public_get("/api/v3/ticker/price", {"symbol": symbol})
        price = Decimal(str(data["price"]))
        logging.info(f"[PRICE] {symbol}: {price}")
        return price
    except Exception as e:
        logging.exception(f"Failed to fetch current price for {symbol}: {e}")
        raise

# -------------------------
# Spot functions (unchanged)
# -------------------------
def get_spot_asset_free(asset: str) -> Decimal:
    """
    Return free balance for asset from spot account as Decimal.
    """
    try:
        params = {"timestamp": get_timestamp_ms()}
        qs_sig = sign_query(params)
        url = f"https://api.binance.com/api/v3/account?{qs_sig}"
        headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
        r = requests.get(url, headers=headers)
        res = r.json()
        if isinstance(res, dict) and res.get("code", 0) < 0:
            raise Exception(f"Binance API error: {res.get('msg')}")
        balances = res.get("balances", [])
        for b in balances:
            if b.get("asset") == asset:
                free = Decimal(str(b.get("free", "0")))
                logging.info(f"[SPOT BAL] {asset} free={free}")
                return free
        return Decimal("0")
    except Exception as e:
        logging.exception("Failed to fetch spot asset balance")
        raise

def place_spot_market_order(symbol: str, side: str, quantity: Decimal):
    """
    Place a spot market order (signed). quantity passed as Decimal or string.
    """
    try:
        params = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": str(quantity),
            "timestamp": get_timestamp_ms()
        }
        qs_sig = sign_query(params)
        url = f"https://api.binance.com/api/v3/order?{qs_sig}"
        headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
        logging.info(f"[SPOT] Sending {side} order for {symbol}, qty={quantity}")
        r = requests.post(url, headers=headers)
        res = r.json()
        logging.info(f"[SPOT RESPONSE] {res}")
        if isinstance(res, dict) and res.get("code", 0) < 0:
            raise Exception(f"Binance API error: {res.get('msg')}")
        return res
    except Exception:
        logging.exception("Spot order failed")
        raise

# -------------------------
# Cross-margin functions
# -------------------------
def get_margin_account():
    """Return cross-margin account details."""
    return signed_get("/sapi/v1/margin/account", {})

def get_margin_asset(asset: str):
    """
    Returns dict with keys 'asset','free','locked','borrowed','interest' for cross-margin asset.
    """
    acct = get_margin_account()
    user_assets = acct.get("userAssets", [])
    for a in user_assets:
        if a.get("asset") == asset:
            # convert fields to Decimal
            return {
                "asset": a.get("asset"),
                "free": Decimal(str(a.get("free", "0"))),
                "locked": Decimal(str(a.get("locked", "0"))),
                "borrowed": Decimal(str(a.get("borrowed", "0"))),
                "interest": Decimal(str(a.get("interest", "0")))
            }
    return {"asset": asset, "free": Decimal("0"), "locked": Decimal("0"), "borrowed": Decimal("0"), "interest": Decimal("0")}

def margin_loan(asset: str, amount: Decimal):
    """
    Borrow asset for cross-margin account.
    POST /sapi/v1/margin/loan
    """
    params = {"asset": asset, "amount": str(amount)}
    return signed_post("/sapi/v1/margin/loan", params)

def margin_repay(asset: str, amount: Decimal):
    """
    Repay borrowed asset for cross-margin account.
    POST /sapi/v1/margin/repay
    """
    params = {"asset": asset, "amount": str(amount)}
    return signed_post("/sapi/v1/margin/repay", params)

def place_margin_market_order(symbol: str, side: str, quantity: Decimal):
    """
    Place cross-margin market order.
    POST /sapi/v1/margin/order
    """
    params = {
        "symbol": symbol,
        "side": side,
        "type": "MARKET",
        "quantity": str(quantity),
    }
    return signed_post("/sapi/v1/margin/order", params)

# -------------------------
# Flask hooks and health endpoints
# -------------------------
@app.before_request
def before_req():
    if should_log_request():
        logging.info(f"[REQUEST] Method:'{request.method}', Path:'{request.path}'")

@app.after_request
def after_req(response):
    if should_log_request():
        logging.info(f"[RESPONSE] Method:'{request.method}', Path:'{request.path}' -> Status Code:'{response.status_code}'")
    return response
    
@app.route('/', methods=['GET', 'HEAD'])
def root():
    # logging.info(f"[ROOT] Call to root endpoint received.")
    # return '', 204
    return jsonify({"status": "rooty"}), 200

@app.route('/ping', methods=['GET'])
def ping():
    # logging.info("[PING] Keep-alive ping received.")
    return "pong", 200

@app.route('/health-check', methods=['GET', 'HEAD'])
def health_check():
    # logging.info("[HEALTH CHECK] Call to health-check endpoint received.")
    return jsonify({"status": "healthy"}), 200

@app.route('/healthz', methods=['GET', 'HEAD'])
def healthz():
    # logging.info("[HEALTHZ CHECK] Call to healthz endpoint received.")
    return jsonify({"status": "healthzy"}), 200

# -------------------------
# Webhook endpoint
# -------------------------
@app.route('/to-the-moon', methods=['POST'])
def webhook():
    logging.info("=====================start=====================")
    try:
        data = request.get_json(force=False, silent=False)
        if not isinstance(data, dict):
            raise ValueError("Payload is not a valid JSON object.")
    except Exception as e:
        raw = request.data.decode("utf-8", errors="ignore")
        logging.exception(f"[FATAL ERROR] Failed to parse JSON payload: {e}")
        logging.info(f"[RAW DATA]\n{raw}")
        return jsonify({"error": "Invalid JSON payload"}), 400
    
    # Validate and parse JSON payload
    data = request.get_json(force=False, silent=False)
    if not isinstance(data, dict):
        raise ValueError("Payload is not a valid JSON object.")

    # Log without secret
    data_for_log = {k: v for k, v in data.items() if k != SECRET_FIELD}
    logging.info(f"[WEBHOOK] Received payload (no {SECRET_FIELD}): {data_for_log}")

    # Secret validation
    secret_from_request = data.get(SECRET_FIELD)
    if not secret_from_request or not hmac.compare_digest(secret_from_request, WEBHOOK_SECRET):
        logging.info(f"[SECURITY] Unauthorized access attempt. Invalid or missing {SECRET_FIELD}.")
        return jsonify({"error": "Unauthorized"}), 401

    # Field Extraction
    try:
        action = data.get("action", "").strip().upper()
        symbol = data.get("symbol", "BTCUSDT").strip().upper()
        buy_pct_raw = data.get("buy_pct", DEFAULT_BUY_PCT)

        is_buy = action == "BUY"
        is_buy_small_btc = action == "BUY_BTC_SMALL"
        is_sell = action == "SELL"
    except Exception as parse_err:
        logging.exception(f"Failed to parse required fields: {parse_err}")
        return jsonify({"error": "Invalid field formatting"}), 400

    # Info log
    info = f"Action: {action}, Symbol: {symbol}"
    if is_buy or is_buy_small_btc:
        info += f", Buy %: {buy_pct_raw}"
    logging.info(info)

    # Validate action
    if action not in {"BUY", "BUY_BTC_SMALL", "SELL"}:
        logging.error(f"Invalid action received: {action}")
        return jsonify({"error": "Invalid action"}), 400

    # Validate symbol
    if symbol not in ALLOWED_SYMBOLS:
        logging.error(f"Symbol '{symbol}' is not in allowed list.")
        return jsonify({"error": f"Symbol '{symbol}' is not allowed"}), 400

    if is_buy or is_buy_small_btc:
        try:
            buy_pct = Decimal(str(buy_pct_raw))
            if not (Decimal("0") < buy_pct <= Decimal("1")):
                raise ValueError("Out of range")
        except Exception:
            buy_pct = DEFAULT_BUY_PCT
            logging.warning(f"Invalid 'buy_pct' provided ({buy_pct_raw}). Defaulting to {DEFAULT_BUY_PCT} (= 0.1 %)")

        try:
            usdt_balance = get_asset_balance("USDT")
            invest_usdt = Decimal(str(usdt_balance)) * buy_pct
            price = Decimal(str(get_current_price(symbol)))
            raw_quantity = (invest_usdt / price).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
            logging.info(f"USDT Balance: {usdt_balance:.4f}, Invest {buy_pct*100:.2f}%: {invest_usdt:.4f}")
            logging.info(f"{symbol} Price: {price}, Raw quantity before step size rounding: {raw_quantity}")

            # Fetch filters and extract stepSize
            filters = get_symbol_filters(symbol)
            step_size = get_filter_value(filters, "LOT_SIZE", "stepSize")
            logging.info(f"[FILTER] Step size from LOT_SIZE for symbol {symbol}: {step_size}")

            step_sized_quantity = quantize_quantity(invest_usdt / price, step_size)
            logging.info(f"[ORDER] Rounded quantity to conform to LOT_SIZE: {step_sized_quantity}")
        except Exception as e:
            logging.exception(f"Pre-order calculation failed: {str(e)}")
            return jsonify({"error": f"Buy calculation failed: {str(e)}"}), 500

        try:
            place_binance_order(symbol, "BUY", step_sized_quantity)
            logging.info(f"[ORDER] BUY executed: {step_sized_quantity} {symbol} at {price} on {datetime.now(timezone.utc).isoformat()}")
            response = jsonify({"status": f"Bought {step_sized_quantity} {symbol}"}), 200
            logging.info(f"Buy order completed successfully, returning response: {response}")
            logging.info("=====================end=====================")
            return response
        except Exception as e:
            logging.exception(f"Failed to place buy order: {str(e)}")
            return jsonify({"error": f"Order failed: {str(e)}"}), 500
        
    if is_sell:
        base_asset = symbol.replace("USDT", "")
        try:
            asset_balance = Decimal(str(get_asset_balance(base_asset)))
            if asset_balance > 0:
                # Fetch filters and extract stepSize
                filters = get_symbol_filters(symbol)
                step_size = get_filter_value(filters, "LOT_SIZE", "stepSize")
                logging.info(f"[FILTER] Step size from LOT_SIZE for symbol {symbol}: {step_size}")

                # Round down to conform to Binance stepSize rules
                quantity = quantize_quantity(asset_balance, step_size)
                logging.info(f"[ORDER] Rounded sell quantity to conform to LOT_SIZE: {quantity}")

                if quantity <= Decimal("0"):
                    logging.warning("Rounded sell quantity is zero or below minimum tradable size. Aborting.")
                    response = jsonify({"warning": "Sell amount too small after rounding."}), 200
                    logging.info(f"Sell attempt aborted due to to a balance below the minimum size, returning response: {response}")
                    logging.info("=====================end=====================")
                    return response

                try:
                    place_binance_order(symbol, "SELL", quantity)
                    price = Decimal(str(get_current_price(symbol)))
                    logging.info(f"[ORDER] SELL executed: {quantity} {symbol} at {price} on {datetime.now(timezone.utc).isoformat()}")
                    response = jsonify({"status": f"Sold {quantity} {symbol}"}), 200
                    logging.info(f"Sell order completed successfully, returning response: {response}")
                    logging.info("=====================end=====================")
                    return response
                except Exception as e:
                    logging.exception(f"Failed to place sell order: {str(e)}")
                    return jsonify({"error": f"Order failed: {str(e)}"}), 500
            else:
                logging.warning("No asset balance to sell.")
                response = jsonify({"warning": "No asset to sell"}), 200
                logging.info(f"Sell attempt aborted due to empty balance, returning response: {response}")
                logging.info("=====================end=====================")
                return response
        except Exception as e:
            logging.exception(f"Sell pre-check failed: {str(e)}")
            return jsonify({"error": f"Sell preparation failed: {str(e)}"}), 500

    

    

def place_binance_order(symbol, side, quantity):
    url = "https://api.binance.com/api/v3/order"
    params = {
        "symbol": symbol,
        "side": side,
        "type": "MARKET",
        "quantity": str(quantity),
        "timestamp": get_timestamp_ms()
    }
    headers = {
        "X-MBX-APIKEY": BINANCE_API_KEY
    }
    query_string = '&'.join([f"{k}={v}" for k, v in params.items()])
    signature = hmac.new(BINANCE_SECRET_KEY.encode(), query_string.encode(), hashlib.sha256).hexdigest()
    params["signature"] = signature
    logging.info(f"[REQUEST] Sending {side} order to Binance for {symbol}, Quantity: {quantity}")
    response = requests.post(url, headers=headers, params=params)
    result = response.json()
    logging.info(f"[BINANCE RESPONSE] {result}")

    # Handle Binance API error (in place_binance_order)
    if "code" in result and result["code"] < 0:
        raise Exception(f"[ERROR] Binance API error: {result.get('msg', 'Unknown error')}")

def get_asset_balance(asset):
    try:
        url = "https://api.binance.com/api/v3/account"
        timestamp = get_timestamp_ms()
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
        # log_balances(balances)
        for b in balances:
            if b["asset"] == asset:
                logging.info(f"[BALANCE] {asset} free balance: {b['free']}")
                return float(b["free"])
            
        logging.warning(f"{asset} balance not found.")
        return 0.0
    
    except Exception as e:
        logging.exception(f"Failed to fetch asset balance: {e}")
        return 0.0


# -------------------------
# maybe useful in future
# -------------------------
def log_filters(symbol, filters):
    logging.info(f"Filters for {symbol}:")
    for f in filters:
        logging.info(f"  - {f['filterType']}: {f}")

def log_balances(balances):
    logging.info("Listing all balances returned by Binance with a Total greater than 0:")
    for b in balances:
        current_asset = b["asset"]
        free = float(b.get("free", 0))
        locked = float(b.get("locked", 0))
        total = free + locked
        if total > 0:
            logging.info(f"[BALANCE] {current_asset} - Total: {total}, Free: {free}, Locked: {locked}")

# -------------------------
# Run app
# -------------------------
if __name__ == '__main__':
    if PORT:
        try:
            PORT = int(PORT)
        except ValueError:
            raise RuntimeError("Environment variable PORT must be an integer.")
    else:
        PORT = 5050 # Default for local dev
    app.run(host='0.0.0.0', port=PORT)
