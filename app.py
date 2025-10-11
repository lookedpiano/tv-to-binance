from flask import Flask, request, jsonify
import hmac
import requests
import time
import os
import logging
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from datetime import datetime, timezone
from requests.exceptions import HTTPError
from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceRequestException



# -------------------------
# Configuration
# -------------------------
from config._settings import (
    ALLOWED_TRADE_TYPES,
    ALLOWED_SYMBOLS,
    ALLOWED_FIELDS,
    REQUIRED_FIELDS,
    SECRET_FIELD,
    WEBHOOK_REQUEST_PATH,
)

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
# Utilities
# -------------------------
def should_log_request():
    return request.path not in ('/health-check', '/healthz', '/ping', '/')

def log_webhook_payload(data: dict, secret_field: str):
    """Log the incoming webhook payload without leaking the secret field."""
    data_for_log = {k: v for k, v in data.items() if k != secret_field}
    logging.info(f"[WEBHOOK] Received payload: {data_for_log}")

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

def quantize_down(value: Decimal, precision: str) -> Decimal:
    """
    Quantize a Decimal value to the given precision string,
    rounding down to avoid exceeding allowed precision.

    Example:
        quantize_down(Decimal("1.23456789"), "0.00000001")
        -> Decimal("1.23456789")
    """
    return value.quantize(Decimal(precision), rounding=ROUND_DOWN)

def display_decimal(value: Decimal, places: int) -> str:
    """
    Return a string of the Decimal truncated to `places` decimal places.
    Safe for display/logging (does not affect the underlying value).
    """
    quantizer = Decimal("1").scaleb(-places)  # e.g. places=16 → Decimal("0.0000000000000001")
    return str(value.quantize(quantizer, rounding=ROUND_DOWN))

def log_webhook_delimiter(at_point: str):
    line = f" Webhook {at_point} "
    border = "─" * (len(line) + 2)
    logging.info(f"┌{border}┐")
    logging.info(f"│ {line} │")
    logging.info(f"└{border}┘")

def log_parsed_payload(action, symbol, buy_pct_raw, buy_amt_raw, sell_pct_raw, sell_amt_raw, trade_type):
    """
    Logs the parsed payload fields.
    Shows buy_pct and buy_amount for BUY actions and sell_pct and sell_amount for SELL actions.
    """
    # Base log
    log_msg = f"[PARSE] action={action}, symbol={symbol}, type={trade_type}"

    # Action-specific logging
    if action == "BUY":
        log_msg += f", buy_pct={buy_pct_raw}, buy_amount={buy_amt_raw}"
    elif action == "SELL":
        log_msg += f", sell_pct={sell_pct_raw}, sell_amount={sell_amt_raw}"

    logging.info(log_msg)

def load_ip_file(path):
    try:
        with open(path) as f:
            return {line.strip() for line in f if line.strip()}
    except FileNotFoundError:
        logging.warning(f"[SECURITY] IP file {path} not found")
        return set()
    
def run_webhook_validations():
    # Outbound IP validation
    valid_ip, error_response = validate_outbound_ip_address()
    if not valid_ip:
        return None, error_response

    # JSON validation
    data, error_response = validate_json()
    if not data:
        return None, error_response

    # Field validation
    valid_fields, error_response = validate_fields(data)
    if not valid_fields:
        return None, error_response

    # Secret validation
    valid_secret, error_response = validate_secret(data)
    if not valid_secret:
        return None, error_response

    return data, None

def split_symbol(symbol: str):
    """
    Splits a trading symbol into (base_asset, quote_asset).
    Works for BTCUSDT, ETHUSDC, etc.
    Assumes symbol ends with a known stablecoin suffix.
    """
    known_quotes = ("USDT", "USDC")
    for q in known_quotes:
        if symbol.endswith(q):
            return symbol[:-len(q)], q
    raise ValueError(f"Unknown quote asset in symbol: {symbol}")


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
        return None, (jsonify({"error": "Invalid JSON payload"}), 400)

def validate_secret(data):
    """Validate that the webhook secret from TradingView matches the expected value."""
    secret_from_request = data.get(SECRET_FIELD)

    if not secret_from_request:
        logging.warning("[SECURITY] Missing secret field")
        return False, (jsonify({"error": "Unauthorized"}), 401)

    # Timing-sicherer Vergleich
    if not hmac.compare_digest(secret_from_request, WEBHOOK_SECRET):
        logging.warning("[SECURITY] Unauthorized attempt (invalid secret)")
        return False, (jsonify({"error": "Unauthorized"}), 401)

    return True, None

def validate_order_qty(symbol: str, qty: Decimal, price: Decimal, min_qty: Decimal, min_notional: Decimal) -> tuple[bool, dict, int]:
    """
    Validate order quantity and notional against exchange filters.
    Returns (is_valid, response_dict, http_status).
    If invalid, response_dict contains a warning.
    """

    logging.info(f"[SAFEGUARDS] Validate order qty for {symbol}: {qty}")
    if qty <= Decimal("0"):
        logging.warning("Trade qty is zero or negative after rounding. Aborting.")
        return False, {"warning": "Calculated trade size too small after rounding"}, 200

    if qty < min_qty:
        logging.warning(f"Trade qty {qty} is below min_qty {min_qty}. Aborting.")
        return False, {"warning": f"Trade qty {qty} is below min_qty {min_qty}"}, 200

    if price is not None:
        if (qty * price) < min_notional:
            logging.warning(f"Trade notional {qty*price} is below min_notional {min_notional}. Aborting.")
            return False, {"warning": f"Trade notional {qty*price} is below min_notional {min_notional}"}, 200
    else:
        logging.info(f"[SAFEGUARDS] Skipping notional validation for {symbol} because price is None.")

    # Successfully validated
    return True, {}, 200

def validate_and_normalize_trade_fields(action: str, is_buy: bool, buy_pct_raw, buy_amt_raw, sell_pct_raw, sell_amt_raw):
    """
    Validates and normalizes trade fields for BUY or SELL.
    - For BUY: exactly one of (buy_pct_raw, buy_amt_raw) must be provided.
    - For SELL: exactly one of (sell_pct_raw, sell_amt_raw) must be provided.
    - Pct must be in (0, 1].
    - Amount must be positive Decimal.
    
    Returns:
        (pct: Decimal | None,
         amt: Decimal | None,
         error_response: Response | None)
    """

    # Select relevant fields based on action
    pct_raw = buy_pct_raw if is_buy else sell_pct_raw
    amt_raw = buy_amt_raw if is_buy else sell_amt_raw
    pct_name = "buy_pct" if is_buy else "sell_pct"
    amt_name = "buy_amount" if is_buy else "sell_amount"

    # Case 1: both provided → reject
    if pct_raw is not None and amt_raw is not None:
        logging.error(f"Both {pct_name} and {amt_name} provided — only one is allowed.")
        return None, None, (jsonify({"error": f"Please provide either {pct_name} or {amt_name}, not both."}), 400)

    # Case 2: neither provided → reject
    if pct_raw is None and amt_raw is None:
        logging.error(f"Neither {pct_name} nor {amt_name} provided — one is required for a {action} order.")
        return None, None, (jsonify({"error": f"Please provide either {pct_name} or {amt_name}."}), 400)

    # If pct provided → check numeric & range
    if pct_raw is not None:
        try:
            pct = Decimal(str(pct_raw))
            if not (Decimal("0") < pct <= Decimal("1")):
                logging.error(f"{pct_name} out of range: {pct_raw}")
                return None, None, (jsonify({"error": f"{pct_name} must be a number between 0 and 1."}), 400)
            return pct, None, None
        except (InvalidOperation, ValueError):
            logging.error(f"Invalid {pct_name} value: {pct_raw}")
            return None, None, (jsonify({"error": f"{pct_name} must be a valid number between 0 and 1."}), 400)

    # If amount provided → check numeric & positive
    if amt_raw is not None:
        try:
            amt = Decimal(str(amt_raw))
            if amt <= 0:
                logging.error(f"{amt_name} must be positive, got: {amt_raw}")
                return None, None, (jsonify({"error": f"{amt_name} must be greater than zero."}), 400)
            return None, amt, None
        except (InvalidOperation, ValueError):
            logging.error(f"Invalid {amt_name} value: {amt_raw}")
            return None, None, (jsonify({"error": f"{amt_name} must be a valid number."}), 400)
        
def validate_fields(data: dict):
    # 1. Reject unknown fields
    unknown_fields = set(data.keys()) - ALLOWED_FIELDS
    if unknown_fields:
        logging.error(f"Unknown fields in payload: {unknown_fields}")
        return False, (jsonify({"error": f"Unknown fields: {list(unknown_fields)}"}), 400)

    # 2. Check required fields
    missing_fields = REQUIRED_FIELDS - set(data.keys())
    if missing_fields:
        logging.error(f"Missing required fields: {missing_fields}")
        return False, (jsonify({"error": f"Missing required fields: {list(missing_fields)}"}), 400)

    return True, None

def validate_outbound_ip_address() -> tuple[bool, tuple | None]:
    """
    Checks if the current outbound IP is in the allowed list.
    Returns (True, None) if allowed, (False, (response, status_code)) if not.
    """
    try:
        current_ip = requests.get("https://api.ipify.org", timeout=21).text.strip()
        logging.info(f"[OUTBOUND_IP] Validate current outbound IP for Binance calls: {current_ip}")

        # Load allowed outbound IPs for Binance calls
        '''
        On October 27th, Render will introduce new outbound IP ranges for each region - OBSERVE

        NEW # remember: up to 30 IPs per API key are allowed on Binance
        "74.220.51.0/24" # the first 24 bits of the address are fixed -> 74.220.51.0, 74.220.51.1, ..., 74.220.51.255 -> 256 ips
        "74.220.59.0/24" # the first 24 bits of the address are fixed -> 74.220.59.0, 74.220.59.1, ..., 74.220.59.255 -> 256 ips
        '''
        ALLOWED_OUTBOUND_IPS = load_ip_file("config/outbound_ips.txt")

        if current_ip not in ALLOWED_OUTBOUND_IPS:
            logging.warning(f"[SECURITY] Outbound IP {current_ip} not in allowed list")
            return False, (jsonify({"error": f"Outbound IP {current_ip} not allowed"}), 403)
        return True, None
    except Exception as e:
        logging.exception(f"Failed to validate outbound IP: {e}")
        return False, (jsonify({"error": "Could not validate outbound IP"}), 500)


# -------------------------
# Exchange helpers
# -------------------------
def get_symbol_filters(symbol: str):
    """
    Fetch symbol filters (lot size, min notional, etc.) for a given symbol.
    """
    try:
        info = client.get_symbol_info(symbol)
        if not info:
            return []
        filters = info.get("filters", [])
        # log_filters(symbol, filters)
        return filters
    except BinanceAPIException as e:
        logging.error(f"Binance API error while fetching filters for {symbol}: {e.message}")
        return []
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

def get_current_price(symbol: str):
    """
    Fetch current price for a symbol using Binance client.
    """
    try:
        data = client.get_symbol_ticker(symbol=symbol)
        price = Decimal(str(data["price"]))
        logging.info(f"[PRICE] {symbol}: {price}")
        return price
    
    except BinanceAPIException as e:
        logging.error(f"Binance API error while fetching price for {symbol}: {e.message}")
        if e.status_code == 418:
            logging.warning(f"Rate limit hit or temp block for {symbol}:<{e}>")
            #logging.warning(f"Rate limit hit or temp block for {symbol}. Retrying in 5s...")
            #time.sleep(5)
            # retry once
            #return get_current_price(symbol)
            return None
        if e.status_code == 429:
            logging.warning(f"Request limit hit or temp block for {symbol}:<{e}>")
            return None
        else:
            logging.exception(f"HTTP error for {symbol}:<{e}>")
            return None  # or raise again if you want it to bubble up
    
    except BinanceRequestException as e:
        # Network or connection issues
        logging.error(f"Binance request error for {symbol}: {e}")
        return None
    
    except Exception as e:
        logging.exception(f"Unexpected error fetching price for {symbol}: {e}")
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
    except Exception as e:
        logging.exception(f"Failed to fetch spot asset balance for {asset}: {e}")
        raise

def place_spot_market_order(symbol, side, quantity):
    return client.order_market(symbol=symbol, side=side, quantity=float(quantity))

def resolve_trade_amount(free_balance: Decimal, amt: Decimal | None, pct: Decimal | None, side: str) -> tuple[Decimal | None, str | None]:
    """
    Resolve the actual trade amount based on either pct or amt.
    - For BUY: free_balance is the quote asset (e.g., USDT balance).
    - For SELL: free_balance is the base asset (e.g., ADA balance).
    - If amt > free_balance, return a warning and abort the order
    Returns: (resolved_amount, error_msg)
    """
    if amt is not None:
        if amt > free_balance:
            logging.warning(f"[INVEST:{side}-AMOUNT] Balance insufficient: requested={amt}, available={free_balance}")
            return None, f"Balance insufficient: requested={amt}, available={free_balance}"
        logging.info(f"[INVEST:{side}-AMOUNT] Using explicit amount={amt}")
        return amt, None

    # pct path
    resolved_amt = quantize_down(free_balance * pct, "0.00000001")
    logging.info(f"[INVEST:{side}-PERCENTAGE] Using pct={float(pct)}, resolved_amt={resolved_amt}")
    return resolved_amt, None

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

    logging.info(f"[ORDER] {side} successfully executed: {qty} {symbol} at {price} on {datetime.now(timezone.utc).isoformat()}")
    return {"status": f"spot_{side.lower()}_executed", "order": resp}, 200



# ---------------------------------
# Unified trade execution
# ---------------------------------
def execute_trade(symbol: str, side: str, pct=None, amt=None, trade_type: str ="SPOT", place_order_fn=None):
    """
    Unified trade executor for SPOT, with the potential to extend to Cross-Margin in the future.
    - Handles buy/sell, quantity math, filter validation, and order placement.
    and we additionally repay any USDT debt using the sale proceeds.
    Returns (response_dict, http_status).
    """
    try:
        price = None

        # Fetch price if needed
        if side == "BUY":
            price = get_current_price(symbol)
            if price is None:
                logging.info(f"Retrying once for {symbol}. Retrying in 3 seconds...")
                time.sleep(3)
                price = get_current_price(symbol)
            if price is None:
                logging.warning(f"No price available for {symbol}. Cannot proceed.")
                return {"error": f"Price not available for {symbol}"}, 200

        # Fetch filters
        step_size, min_qty, min_notional = get_trade_filters(symbol)
        if None in (step_size, min_qty, min_notional):
            logging.warning(f"Incomplete trade filters for {symbol}: step_size={step_size}, min_qty={min_qty}, min_notional={min_notional}")
            return {"error": f"Filters not available for {symbol}"}, 200

        try:
            base_asset, quote_asset = split_symbol(symbol)
        except ValueError as e:
            logging.error("Failed to parse base/quote assets")
            return {"error": f"Failed to parse base/quote assets: {str(e)}"}, 400

        # BUY flow
        if side == "BUY":
            try:
                quote_free = get_spot_asset_free(quote_asset)
                invest_amount, error_msg = resolve_trade_amount(quote_free, amt, pct, side="BUY")
                if error_msg:
                    logging.warning(f"[INVEST ERROR] {error_msg}")
                    return {"error": error_msg}, 200
                raw_qty = invest_amount / price
                qty = quantize_quantity(raw_qty, step_size)
                logging.info(f"[EXECUTE SPOT BUY] {symbol}: invest_amount={invest_amount}, qty={qty}, raw_qty={display_decimal(raw_qty, 16)}")
                logging.info(f"[INVESTMENT] Approx. total investment ≈ {(qty * price):.2f} USDT --> price={price}, qty={qty}")
                is_valid, resp_dict, status = validate_order_qty(symbol, qty, price, min_qty, min_notional)
                if not is_valid:
                    return resp_dict, status
                
                # Place the order after safeguards pass
                return place_order_with_handling(symbol, side, qty, price, place_order_fn)
                            
            except Exception as e:
                logging.exception("Spot buy failed")
                return {"error": f"Spot buy failed: {str(e)}"}, 500

        # SELL flow
        elif side == "SELL":
            try:
                asset_free = get_spot_asset_free(base_asset)
                if asset_free <= Decimal("0"):
                    logging.warning(f"No spot {base_asset} balance to sell. Aborting.")
                    return {"warning": f"No spot {base_asset} balance to sell. Aborting."}, 200
                sell_qty, error_msg = resolve_trade_amount(asset_free, amt, pct, side="SELL")
                if error_msg:
                    logging.warning(f"[INVEST ERROR] {error_msg}")
                    return {"error": error_msg}, 200
                qty = quantize_quantity(sell_qty, step_size)
                logging.info(f"[EXECUTE SPOT SELL] {symbol}: asset_free={asset_free}, sell_qty={qty}, step_size={step_size}, min_qty={min_qty}, min_notional={min_notional}")
                #logging.info(f"[PROCEEDS] Approx. total proceeds ≈ {(qty * price):.2f} {quote_asset} --> price={price}, qty={qty}")
                is_valid, resp_dict, status = validate_order_qty(symbol, qty, price, min_qty, min_notional)
                if not is_valid:
                    return resp_dict, status
                
                # Place the order after safeguards pass
                return place_order_with_handling(symbol, side, qty, price, place_order_fn)
            
            except Exception as e:
                logging.exception("Spot sell failed")
                return {"error": f"Spot sell failed: {str(e)}"}, 500

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

        # Load TradingView IPs
        # Allowlist of known TradingView alert IPs (must keep updated), see: https://www.tradingview.com/support/solutions/43000529348
        TRADINGVIEW_IPS = load_ip_file("config/tradingview_ips.txt")

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
    log_webhook_delimiter("START")
    start_time = time.perf_counter()

    try:
        data, error_response = run_webhook_validations()
        if error_response:
            return error_response

        # Log payload without secret
        log_webhook_payload(data, SECRET_FIELD)
        
        # Parse fields
        try:
            action = data.get("action", "").strip().upper()
            symbol = data.get("symbol", "").strip().upper()
            buy_pct_raw = data.get("buy_pct", None)
            buy_amt_raw = data.get("buy_amount", None)
            sell_pct_raw = data.get("sell_pct", None)
            sell_amt_raw = data.get("sell_amount", None)
            trade_type = data.get("type", "SPOT").strip().upper()
        except Exception as e:
            logging.exception("Failed to extract fields")
            return jsonify({"error": "Invalid fields"}), 400

        log_parsed_payload(action, symbol, buy_pct_raw, buy_amt_raw, sell_pct_raw, sell_amt_raw, trade_type)

        # Easter egg check
        resp = detect_tradingview_placeholder(action)
        if resp:
            return resp
        
        # Validate action and symbol
        if action not in {"BUY", "SELL"}:
            logging.error(f"Invalid action: {action}")
            return jsonify({"error": "Invalid action"}), 400
        if trade_type not in ALLOWED_TRADE_TYPES:
            logging.error(f"Invalid trade_type: {trade_type}")
            return jsonify({"error": f"Invalid trade_type: {trade_type}"}), 400
        if symbol not in ALLOWED_SYMBOLS:
            logging.error(f"Symbol not allowed: {symbol}")
            return jsonify({"error": "Symbol not allowed"}), 400

        is_buy = action == "BUY"
        pct, amt, error_response = validate_and_normalize_trade_fields(
            action, is_buy, buy_pct_raw, buy_amt_raw, sell_pct_raw, sell_amt_raw
        )
        if error_response:
            return error_response

        result, status_code = execute_trade(
            symbol=symbol,
            side="BUY" if is_buy else "SELL",
            pct=pct,
            amt=amt,
            trade_type=trade_type,
            place_order_fn=place_spot_market_order
        )
        return jsonify(result), status_code
    
    finally:
        end_time = time.perf_counter()
        elapsed = end_time - start_time
        log_webhook_delimiter(f"END (elapsed: {elapsed:.4f} seconds)")


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
        logging.warning("TradingView placeholder received instead of explicit action.")
        logging.warning("Did you accidentally paste {{strategy.order.action}} instead of letting TradingView expand it? Use BUY or SELL instead...")
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
