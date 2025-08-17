from flask import Flask, request, jsonify
import hmac, hashlib
import requests
import time
import os
import logging
from decimal import Decimal, ROUND_DOWN
from datetime import datetime, timezone
from requests.exceptions import HTTPError
from binance.client import Client
from binance.exceptions import BinanceAPIException


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


# -----------------------------
# CLIENT INIT
# -----------------------------
client = Client(BINANCE_API_KEY, BINANCE_SECRET_KEY)


# -------------------------
# Configuration
# -------------------------
ALLOWED_SYMBOLS = {"BTCUSDT", "ETHUSDT", "ADAUSDT", "DOGEUSDT", "PEPEUSDT", "XRPUSDT", "WIFUSDT"}
DEFAULT_BUY_PCT = Decimal("0.001") # 0.1 %
SECRET_FIELD = "client_secret"
WEBHOOK_REQUEST_PATH = "/to-the-moon"
MAX_REQUEST_AGE = 10  # seconds
# Allowlist of known TradingView alert IPs (must keep updated)
# See: https://www.tradingview.com/support/solutions/43000529348
TRADINGVIEW_IPS = {
    "52.89.214.238",
    "34.212.75.30",
    "54.218.53.128",
    "52.32.178.7"
}


# -------------------------
# Utilities
# -------------------------
def should_log_request():
    return request.path not in ('/health-check', '/healthz', '/ping', '/')

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


# -----------------------
# Validation functions
# -----------------------
def validate_json():
    """Validate that the incoming request contains valid JSON."""
    try:
        data = request.get_json(force=False, silent=False)
        if not isinstance(data, dict):
            raise ValueError("Payload is not a valid JSON object.")
        return data, None
    except Exception as e:
        raw = request.data.decode("utf-8", errors="ignore")
        logging.exception(f"[FATAL ERROR] Failed to parse JSON payload: {e}")
        logging.info(f"[RAW DATA]\n{raw}")
        return None, jsonify({"error": "Invalid JSON payload"}), 400

def validate_timestamp(data):
    """Validate that the timestamp exists, is a proper ISO 8601 string, and is recent."""
    timestamp = data.get("timestamp")
    if not timestamp:
        logging.warning("[TIMESTAMP] Missing timestamp")
        return False, jsonify({"error": "Missing timestamp"}), 400

    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))  # Z = UTC
        ts = int(dt.timestamp())
    except ValueError:
        logging.warning("[TIMESTAMP] Invalid timestamp format")
        return False, jsonify({"error": "Invalid timestamp"}), 400

    now = int(time.time())
    if abs(now - ts) > MAX_REQUEST_AGE:
        logging.warning("[TIMESTAMP] Request expired")
        return False, jsonify({"error": "Request expired"}), 401
    return True, ts, None

def validate_secret(data):
    """Validate that the webhook secret is correct."""
    secret_from_request = data.get(SECRET_FIELD)
    if not secret_from_request or not hmac.compare_digest(str(secret_from_request), str(WEBHOOK_SECRET)):
        logging.warning("[SECURITY] Unauthorized attempt (invalid or missing secret)")
        return False, jsonify({"error": "Unauthorized"}), 401
    return True, None

def validate_order_qty(qty: Decimal, price: Decimal, min_qty: Decimal, min_notional: Decimal) -> tuple[bool, dict, int]:
    """
    Validate order quantity and notional against exchange filters.
    Returns (is_valid, response_dict, http_status).
    If invalid, response_dict contains a warning.
    """
    if qty <= Decimal("0"):
        logging.warning("Trade qty is zero or negative after rounding. Aborting.")
        return False, {"warning": "Calculated trade size too small after rounding"}, 200

    if qty < min_qty:
        logging.warning(f"Trade qty {qty} is below min_qty {min_qty}. Aborting.")
        return False, {"warning": f"Trade qty {qty} is below min_qty {min_qty}"}, 200

    if (qty * price) < min_notional:
        logging.warning(f"Trade notional {qty*price} is below min_notional {min_notional}. Aborting.")
        return False, {"warning": f"Trade notional {qty*price} is below min_notional {min_notional}"}, 200

    return True, {}, 200


# -------------------------
# Binance helper functions
# -------------------------
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

def get_min_notional(filters):
    # Attempt to retrieve the MIN_NOTIONAL filter
    min_notional = next((f['minNotional'] for f in filters if f['filterType'] == 'MIN_NOTIONAL'), None)
    if min_notional:
        return Decimal(min_notional)
    
    # If MIN_NOTIONAL is not found, check for the NOTIONAL filter
    notional_filter = next((f for f in filters if f['filterType'] == 'NOTIONAL'), None)
    if notional_filter:
        return Decimal(notional_filter['minNotional'])
    
    # If neither filter is found, return a default value
    return Decimal('0.0')

def get_trade_filters(symbol):
    """Fetch filters and return step_size, min_qty, min_notional as Decimals or (None, None, None) on failure."""
    filters = get_symbol_filters(symbol)
    if not filters:  # no filters available
        logging.warning(f"No filters found for {symbol}")
        return None, None, None

    try:
        step_size_val = get_filter_value(filters, "LOT_SIZE", "stepSize")
        min_qty_val = get_filter_value(filters, "LOT_SIZE", "minQty")
        
        min_notional = get_min_notional(filters)

        step_size = Decimal(step_size_val)
        min_qty = Decimal(min_qty_val)

        logging.info(f"[FILTERS] step_size={step_size}, min_notional={min_notional}, min_qty={min_qty}")
        return step_size, min_qty, min_notional

    except Exception as e:
        logging.exception(f"Error parsing filters for {symbol}: {e}")
        return None, None, None

def get_current_price(symbol):
    try:
        data = public_get("/api/v3/ticker/price", {"symbol": symbol})
        price = Decimal(str(data["price"]))
        logging.info(f"[PRICE] {symbol}: {price}")
        return price

    except HTTPError as e:
        if e.response.status_code == 418:
            logging.warning(f"Rate limit hit or temp block for {symbol}:<{e}>")
            #logging.warning(f"Rate limit hit or temp block for {symbol}. Retrying in 5s...")
            #time.sleep(5)
            # retry once
            #return get_current_price(symbol)
            return None
        if e.response.status_code == 429:
            logging.warning(f"Request limit hit or temp block for {symbol}:<{e}>")
            return None
        else:
            logging.exception(f"HTTP error for {symbol}:<{e}>")
            return None  # or raise again if you want it to bubble up
    except Exception as e:
        logging.exception(f"Unexpected error fetching price for {symbol}:<{e}>")
        return None


# -------------------------
# Spot functions
# -------------------------
def get_spot_asset_free(asset: str) -> Decimal:
    """
    Return free balance for asset from spot account as Decimal.
    """
    try:
        account_info = client.get_account()
        balances = account_info.get("balances", [])
        # log_balances(balances)
        for b in balances:
            if b.get("asset") == asset:
                free = Decimal(str(b.get("free", "0")))
                logging.info(f"[SPOT BALANCE] {asset} free={free}")
                return free
        return Decimal("0")
    except BinanceAPIException as e:
        logging.error(f"Binance API error while fetching {asset} balance: {e.message}")
        raise
    except Exception:
        logging.exception("Failed to fetch spot asset balance")
        raise

def place_spot_market_order(symbol, side, quantity):
    return client.order_market(symbol=symbol, side=side, quantity=float(quantity))

def resolve_invest_usdt(usdt_free, amt_raw, buy_pct) -> tuple[Decimal | None, str | None]:
    """
    Decide how much USDT to invest.
    
    Returns:
        (invest_usdt, error_message)
        - invest_usdt (Decimal) if valid, else None
        - error_message (str) if invalid, else None
    """
    if amt_raw is not None:
        try:
            amt = Decimal(str(amt_raw))
            if amt <= 0:
                return None, "Amount must be positive."

            if amt > usdt_free:
                logging.warning(f"[INVEST:AMT] Balance insufficient: requested amt={amt}, available={usdt_free}")
                return None, f"Balance insufficient: requested={amt}, available={usdt_free}"

            logging.info(f"[INVEST:AMT] Using explicit amt={amt}")
            return amt, None
        except Exception as e:
            logging.warning(f"[INVEST:AMT] Invalid amt provided ({amt_raw}). Aborting. Error: {e}")
            return None, f"Invalid amt provided: {amt_raw}. Error: {e}"
    
    # Use buy_pct if amt_raw is missing
    invest_usdt = (usdt_free * buy_pct).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
    logging.info(f"[INVEST:PCT] Using buy_pct={buy_pct}, invest_usdt={invest_usdt}")
    return invest_usdt, None

def place_order_with_handling(symbol: str, side: str, qty: Decimal, price: Decimal, place_order_fn):
    """
    Place an order safely with unified exception handling and logging.
    Returns (response_dict, status_code).
    """
    try:
        resp = place_order_fn(symbol, side, qty)
    except requests.exceptions.HTTPError as e:
        err_str = str(e).lower()
        if "418" in err_str or "teapot" in err_str:
            logging.error(f"Binance rate limit hit (418): {e}")
            return {"error": "Binance rate limit hit (418 I'm a teapot)"}, 429
        elif "429" in err_str or "too many requests" in err_str:
            logging.error(f"Binance request limit hit (429): {e}")
            return {"error": "Binance request limit hit (429)"}, 429
        elif "notional" in err_str:
            logging.error("Trade rejected: below Binance min_notional")
            return {"error": "Trade rejected: below Binance min_notional"}, 400
        else:
            raise

    logging.info(f"[ORDER] {side} executed: {qty} {symbol} at {price} on {datetime.now(timezone.utc).isoformat()}")
    return {"status": f"spot_{side.lower()}_executed", "order": resp}, 200


# ---------------------------------
# Unified trade execution
# ---------------------------------
def execute_trade(symbol: str, side: str, trade_type: str ="SPOT", buy_pct_raw=None, amt_raw=None, leverage=None, place_order_fn=None):
    """
    Unified trade executor for SPOT and (future) MARGIN.
    Handles buy/sell, quantity math, filter validation, and order placement.
    Returns (response_dict, http_status).
    """
    try:
        # Fetch price and filters
        price = get_current_price(symbol)
        if price is None:
            logging.info(f"Retrying once for {symbol}. Retrying in 21s...")
            time.sleep(21)
            price = get_current_price(symbol)
        if price is None:
            logging.warning(f"No price available for {symbol}. Cannot proceed.")
            logging.info("=====================end=====================")
            return {"error": f"Price not available for {symbol}"}, 200

        step_size, min_qty, min_notional = get_trade_filters(symbol)
        if None in (step_size, min_qty, min_notional):
            logging.warning(f"Incomplete trade filters for {symbol}: step_size={step_size}, min_qty={min_qty}, min_notional={min_notional}")
            return {"error": f"Filters not available for {symbol}"}, 200

        # BUY flow
        if side == "BUY":
            # Normalize buy_pct
            try:
                buy_pct = Decimal(str(buy_pct_raw))
                if not (Decimal("0") < buy_pct <= Decimal("1")):
                    raise ValueError("buy_pct out of range")
            except Exception:
                buy_pct = DEFAULT_BUY_PCT
                logging.warning(f"Invalid buy_pct provided ({buy_pct_raw}); defaulting to {DEFAULT_BUY_PCT}")

            if trade_type == "SPOT":
                # SPOT buy -> use spot USDT balance
                try:
                    usdt_free = get_spot_asset_free("USDT")
                    invest_usdt, error_msg = resolve_invest_usdt(usdt_free, amt_raw, buy_pct)
                    if error_msg:
                        logging.warning(f"[INVEST ERROR] {error_msg}")
                        return {"error": error_msg}, 200
                    raw_qty = invest_usdt / price
                    qty = quantize_quantity(raw_qty, step_size)
                    logging.info(f"[EXECUTE SPOT BUY] {symbol}: invest={invest_usdt}, final_qty={qty}, raw_qty={raw_qty}")
                    logging.info(f"[SAFEGUARDS] Validate order qty for {symbol} with qty={qty} at price={price}={qty*price}.")
                    is_valid, resp_dict, status = validate_order_qty(qty, price, min_qty, min_notional)
                    if not is_valid:
                        return resp_dict, status
                    
                    # Place the order after safeguards pass
                    return place_order_with_handling(symbol, side, qty, price, place_order_fn)
                                
                except Exception as e:
                    logging.exception("Spot buy failed")
                    return {"error": f"Spot buy failed: {str(e)}"}, 500
            elif trade_type == "MARGIN":
                # MARGIN buy -> operate only on margin account (no spot fallback)
                logging.info("TODO : handle margin buys")
                logging.warning("Margin buy not implemented")
                return {"error": "Margin buy not implemented"}, 501
            else:
                return {"error": f"Unknown trade type {trade_type}"}, 400

        # SELL flow
        elif side == "SELL":
            # We'll use base asset name
            base_asset = symbol.replace("USDT", "")
            if trade_type == "SPOT":
                # Sell on spot account only
                try:
                    base_free = get_spot_asset_free(base_asset)
                    if base_free <= Decimal("0"):
                        logging.warning(f"No spot {base_asset} balance to sell. Aborting.")
                        response = {"warning": f"No spot {base_asset} balance to sell. Aborting."}, 200
                        #logging.info(f"Sell attempt aborted due to empty balance, returning response: {response}")
                        logging.info("=====================end=====================")
                        return response
                    qty = quantize_quantity(base_free, step_size)
                    logging.info(f"[EXECUTE SPOT SELL] {symbol}: base_free={base_free}, sell_qty={qty}, step_size={step_size}, min_qty={min_qty}, min_notional={min_notional}")

                    # Safeguards
                    is_valid, resp_dict, status = validate_order_qty(qty, price, min_qty, min_notional)
                    if not is_valid:
                        return resp_dict, status
                    
                    # Place the order after safeguards pass
                    return place_order_with_handling(symbol, side, qty, price, place_order_fn)
                
                except Exception as e:
                    logging.exception("Spot sell failed")
                    return {"error": f"Spot sell failed: {str(e)}"}, 500

            elif trade_type == "MARGIN":
                # Sell on margin account only. After sell, attempt to repay any borrowed USDT.
                logging.info("TODO : handle margin sells")
                logging.warning("Margin sell not implemented")
                return {"error": "Margin sell not implemented"}, 501
            else:
                return {"error": f"Unknown trade type {trade_type}"}, 400

        # Unknown side (shouldn't happen)
        else:
            return {"error": f"Unknown side {side}. No action performed."}, 400

    except Exception as e:
        logging.exception("Trade execution failed")
        return {"error": f"Trade execution failed: {str(e)}"}, 500


# -------------------------
# Flask hooks and health endpoints
# -------------------------
@app.before_request
def check_ip_whitelist():
    if request.method == "POST" and request.path == WEBHOOK_REQUEST_PATH:
        raw_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        client_ip = raw_ip.split(",")[0].strip() # Take only the first IP in case there are multiple
        if client_ip not in TRADINGVIEW_IPS:
            logging.warning(f"Blocked request from unauthorized IP: {client_ip}")
            logging.warning(f"IP address owned by: https://ipapi.co/{client_ip}/json/")
            return jsonify({"error": f"IP {client_ip} not allowed"}), 403
        
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
@app.route(WEBHOOK_REQUEST_PATH, methods=['POST'])
def webhook():
    logging.info("=====================start=====================")
    
    # JSON validation
    data, error_response = validate_json()
    if not data:
        return error_response
    
    # Timestamp validation
    valid_ts, ts, error_response = validate_timestamp(data)
    if not valid_ts:
        return error_response
    
    # Secret validation
    valid_secret, error_response = validate_secret(data)
    if not valid_secret:
        return error_response

    # Log payload without secret
    data_for_log = {k: v for k, v in data.items() if k != SECRET_FIELD}
    logging.info(f"[WEBHOOK] Received payload (no {SECRET_FIELD}): {data_for_log}")
    
    # Parse fields
    try:
        action = data.get("action", "").strip().upper()
        symbol = data.get("symbol", "").strip().upper()
        buy_pct_raw = data.get("buy_pct", DEFAULT_BUY_PCT)
        trade_type = data.get("type", "SPOT").strip().upper()  # MARGIN or SPOT
        leverage_raw = data.get("leverage", None)
        amt_raw = data.get("amt", None)
    except Exception as e:
        logging.exception("Failed to extract fields")
        return jsonify({"error": "Invalid fields"}), 400

    logging.info(f"[PARSE] action={action}, symbol={symbol}, type={trade_type}, leverage={leverage_raw}, buy_pct={buy_pct_raw}, amt={amt_raw}")

    # Easter egg check
    resp = detect_tradingview_placeholder(action)
    if resp:
        return resp
    
    # Validate action and symbol
    if action not in {"BUY", "BUY_BTC_SMALL", "SELL"}:
        logging.error(f"Invalid action: {action}")
        return jsonify({"error": "Invalid action"}), 400
    if symbol not in ALLOWED_SYMBOLS:
        logging.error(f"Symbol not allowed: {symbol}")
        return jsonify({"error": "Symbol not allowed"}), 400

    is_buy = action in {"BUY", "BUY_BTC_SMALL"}

    result, status_code = execute_trade(
        symbol=symbol,
        side="BUY" if is_buy else "SELL",
        trade_type=trade_type,
        buy_pct_raw=buy_pct_raw if is_buy else None,
        amt_raw=amt_raw,
        leverage=leverage_raw,
        place_order_fn=place_spot_market_order
    )
    logging.info("=====================end=====================")
    return jsonify(result), status_code


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
# easter egg
# -------------------------
def detect_tradingview_placeholder(action: str):
    """
    Detect if the action is still the raw TradingView placeholder.
    Returns a Flask response if placeholder is found, otherwise None.
    """
    if action == "{{STRATEGY.ORDER.ACTION}}":
        logging.warning("TradingView placeholder received instead of expanded action.")
        logging.info("Did you accidentally paste {{strategy.order.action}} instead of letting TradingView expand it? Use BUY or SELL instead...")
        return jsonify({"error": "Did you accidentally paste {{strategy.order.action}} instead of letting TradingView expand it?"}), 400
    return None


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
        PORT = 5050  # Default for local dev
    app.run(host='0.0.0.0', port=PORT)
