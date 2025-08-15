from flask import Flask, request, jsonify
import hmac, hashlib
import requests
import time
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
ALLOWED_SYMBOLS = {"BTCUSDT", "ETHUSDT", "ADAUSDT", "DOGEUSDT", "PEPEUSDT", "XRPUSDT", "WIFUSDT"}
DEFAULT_BUY_PCT = Decimal("0.001") # 0.1 %
SECRET_FIELD = "client_secret"
WEBHOOK_REQUEST_PATH = "/to-the-moon"
MAX_REQUEST_AGE = 30  # seconds
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

def get_trade_filters(symbol):
    """Fetch filters and return step_size, min_qty, min_notional as Decimals."""
    filters = get_symbol_filters(symbol)
    step_size = Decimal(get_filter_value(filters, "LOT_SIZE", "stepSize"))
    min_qty = Decimal(get_filter_value(filters, "LOT_SIZE", "minQty"))
    min_notional = Decimal(get_filter_value(filters, "MIN_NOTIONAL", "minNotional"))
    return step_size, min_qty, min_notional


# -------------------------
# Spot functions
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
        # log_balances(balances)
        for b in balances:
            if b.get("asset") == asset:
                free = Decimal(str(b.get("free", "0")))
                logging.info(f"[SPOT BALANCE] {asset} free={free}")
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
        # logging.info(f"[SPOT RESPONSE] {res}")
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
    except Exception as e:
        logging.exception("Failed to extract fields")
        return jsonify({"error": "Invalid fields"}), 400

    logging.info(f"[PARSE] action={action}, symbol={symbol}, type={trade_type}, leverage={leverage_raw}, buy_pct={buy_pct_raw}")

    # Validate action and symbol
    if action not in {"BUY", "BUY_BTC_SMALL", "SELL"}:
        logging.error(f"Invalid action: {action}")
        return jsonify({"error": "Invalid action"}), 400
    if symbol not in ALLOWED_SYMBOLS:
        logging.error(f"Symbol not allowed: {symbol}")
        return jsonify({"error": "Symbol not allowed"}), 400

    is_buy = action in {"BUY", "BUY_BTC_SMALL"}
    is_sell = action == "SELL"

    # Compute price and filters
    try:
        price = get_current_price(symbol)
        step_size, min_qty, min_notional = get_trade_filters(symbol)
    except Exception as e:
        logging.exception("Failed to fetch price/filters")
        return jsonify({"error": "Price/filters fetch failed"}), 500

    # -------------------------
    # BUY flow
    # -------------------------
    if is_buy:
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
                invest_usdt = (usdt_free * buy_pct).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
                raw_qty = invest_usdt / price
                qty = quantize_quantity(raw_qty, step_size)
                logging.info(f"[SPOT BUY] usdt_free={usdt_free}, invest={invest_usdt}, raw_qty={raw_qty}, qty={qty}, step_size={step_size}")

                # Safeguards
                if qty <= Decimal("0"):
                    return jsonify({"warning": "Calculated trade size too small after rounding"}), 200
                if qty < min_qty:
                    return jsonify({"warning": f"Trade qty {qty} is below min_qty {min_qty}"}), 200
                if (qty * price) < min_notional:
                    return jsonify({"warning": f"Trade notional {qty*price} is below min_notional {min_notional}"}), 200
                
                try:
                    resp = place_spot_market_order(symbol, "BUY", qty)
                except requests.exceptions.HTTPError as e:
                    err_str = str(e).lower()
                    if "418" in err_str or "teapot" in err_str:
                        return jsonify({"error": "Binance rate limit hit (418 I'm a teapot)"}), 429
                    elif "notional" in err_str:
                        return jsonify({"error": "Trade rejected: below Binance min_notional"}), 400
                    else:
                        raise

                logging.info(f"[ORDER] BUY executed: {qty} {symbol} at {price} on {datetime.now(timezone.utc).isoformat()}")
                # logging.info(f"Buy order completed successfully, returning response: {resp}")
                logging.info("=====================end=====================")
                return jsonify({"status": "spot_buy_executed", "order": resp}), 200
            
            except Exception as e:
                logging.exception("Spot buy failed")
                return jsonify({"error": f"Spot buy failed: {str(e)}"}), 500

        elif trade_type == "MARGIN":
            # MARGIN buy -> operate only on margin account (no spot fallback)
            logging.info("TODO : in buy margin trades...")
            '''
            try:
                # leverage parsing
                leverage = Decimal(str(leverage_raw)) if leverage_raw is not None else Decimal("1")
                if leverage < 1:
                    logging.warning("Leverage < 1; defaulting to 1")
                    leverage = Decimal("1")
                MAX_LEV = Decimal("10")
                if leverage > MAX_LEV:
                    logging.warning(f"Cap leverage to {MAX_LEV}")
                    leverage = MAX_LEV

                # margin USDT free
                margin_usdt = get_margin_asset("USDT")
                usdt_free = margin_usdt["free"]
                invest_base = (usdt_free * buy_pct).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
                target_exposure = (invest_base * leverage).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
                raw_qty = target_exposure / price
                qty = quantize_quantity(raw_qty, step_size)
                logging.info(f"[MARGIN BUY] usdt_free={usdt_free}, invest_base={invest_base}, leverage={leverage}, target_exposure={target_exposure}, raw_qty={raw_qty}, qty={qty}")

                if qty <= Decimal("0"):
                    return jsonify({"warning": "Calculated quantity too small after rounding"}), 200

                # required USDT for this qty
                required_usdt = (qty * price).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)

                borrow_amt = Decimal("0")
                loan_resp = None
                if usdt_free < required_usdt:
                    borrow_amt = (required_usdt - usdt_free).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
                    logging.info(f"[MARGIN BUY] Borrow required: {borrow_amt}")
                    loan_resp = margin_loan("USDT", borrow_amt)
                    logging.info(f"[MARGIN BUY] Loan response: {loan_resp}")
                else:
                    logging.info("[MARGIN BUY] No borrow required")

                order_resp = place_margin_market_order(symbol, "BUY", qty)
                logging.info(f"[MARGIN BUY] order_resp: {order_resp}")

                # Return order + loan info (no local storage)
                return jsonify({"status": "margin_buy_executed", "order": order_resp, "loan": loan_resp}), 200
            except Exception as e:
                logging.exception("Margin buy failed")
                return jsonify({"error": f"Margin buy failed: {str(e)}"}), 500
            '''
        else:
            return jsonify({"error": "Unknown trade type"}), 400
    
    # -------------------------
    # SELL flow
    # -------------------------
    if is_sell:
        # We'll use base asset name
        base_asset = symbol.replace("USDT", "")
        if trade_type == "SPOT":
            # Sell on spot account only
            try:
                base_free = get_spot_asset_free(base_asset)
                if base_free <= Decimal("0"):
                    logging.warning("No asset balance to sell.")
                    response = jsonify({"warning": "No spot asset balance to sell"}), 200
                    logging.info(f"Sell attempt aborted due to empty balance, returning response: {response}")
                    logging.info("=====================end=====================")
                    return response

                sell_qty = quantize_quantity(base_free, step_size)
                logging.info(f"[SPOT SELL] symbol={symbol}, base_free={base_free}, sell_qty={sell_qty}, step_size={step_size}")

                # Safeguards
                if sell_qty <= Decimal("0"):
                    logging.warning("Rounded sell quantity is zero or below minimum tradable size. Aborting.")
                    response = jsonify({"warning": "Sell amount too small after rounding."}), 200
                    logging.info(f"Sell attempt aborted due to to a balance below the minimum size, returning response: {response}")
                    logging.info("=====================end=====================")
                    return response
                if sell_qty < min_qty:
                    return jsonify({"warning": f"sell_qty {sell_qty} is below min_qty {min_qty}"}), 200
                if (sell_qty * price) < min_notional:
                    return jsonify({"warning": f"Sell notional {sell_qty*price} is below min_notional {min_notional}"}), 200

                try:
                    resp = place_spot_market_order(symbol, "SELL", sell_qty)
                except requests.exceptions.HTTPError as e:
                    err_str = str(e).lower()
                    if "418" in err_str or "teapot" in err_str:
                        return jsonify({"error": "Binance rate limit hit (418 I'm a teapot)"}), 429
                    elif "notional" in err_str:
                        return jsonify({"error": "Trade rejected: below Binance min_notional"}), 400
                    else:
                        raise

                logging.info(f"[ORDER] SELL executed: {sell_qty} {symbol} at {price} on {datetime.now(timezone.utc).isoformat()}")
                # logging.info(f"Sell order completed successfully, returning response: {resp}")
                logging.info("=====================end=====================")
                return jsonify({"status": "spot_sell_executed", "order": resp}), 200
            except Exception as e:
                logging.exception("Spot sell failed")
                return jsonify({"error": f"Spot sell failed: {str(e)}"}), 500
        elif trade_type == "MARGIN":
            # Sell on margin account only. After sell, attempt to repay any borrowed USDT.
            logging.info(" TODO : in sell margin trades... ")
            '''
            # Sell on margin account only. After sell, attempt to repay any borrowed USDT.
            try:
                margin_asset = get_margin_asset(base_asset)
                base_free = margin_asset["free"]
                if base_free <= Decimal("0"):
                    return jsonify({"warning": "No margin asset balance to sell"}), 200

                # filters = get_symbol_filters(symbol) # should be taken from above
                # step_size = get_filter_value(filters, "LOT_SIZE", "stepSize") # should be taken from above
                sell_qty = quantize_quantity(base_free, step_size)
                logging.info(f"[MARGIN SELL] symbol={symbol}, base_free={base_free}, sell_qty={sell_qty}, step_size={step_size}")

                if sell_qty <= Decimal("0"):
                    return jsonify({"warning": "Sell amount too small after rounding"}), 200

                order_resp = place_margin_market_order(symbol, "SELL", sell_qty)
                logging.info(f"[MARGIN SELL] order_resp: {order_resp}")

                # After selling, attempt to repay USDT debt if any
                margin_usdt_info = get_margin_asset("USDT")
                borrowed = margin_usdt_info["borrowed"]
                if borrowed > 0:
                    # Try to repay borrowed amount fully (use repay call)
                    try:
                        repay_resp = margin_repay("USDT", borrowed)
                        logging.info(f"[MARGIN] Repay response: {repay_resp}")
                    except Exception as repay_e:
                        logging.exception("Auto-repay failed after margin sell")
                        return jsonify({"status": "margin_sell_executed", "order": order_resp, "repay_error": str(repay_e)}), 500

                return jsonify({"status": "margin_sell_executed", "order": order_resp}), 200
            except Exception as e:
                logging.exception("Margin sell failed")
                return jsonify({"error": f"Margin sell failed: {str(e)}"}), 500
            
            '''
        else:
            return jsonify({"error": "Unknown trade type"}), 400
    
    # If nothing matched (shouldn't happen)
    return jsonify({"error": "No action performed"}), 400


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
        PORT = 5050  # Default for local dev
    app.run(host='0.0.0.0', port=PORT)
