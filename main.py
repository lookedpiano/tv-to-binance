from flask import Flask, request, jsonify
import hmac, hashlib
import requests
import time
import os
import logging
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from datetime import datetime, timezone
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

def quantize_quantity_old(quantity: Decimal, step_size_str: str) -> Decimal:
    """Round down quantity to conform to stepSize."""
    step = Decimal(step_size_str)
    # floor to step multiple
    quant = (Decimal(quantity) // step) * step
    # quantize to the same scale as step
    return quant.quantize(step, rounding=ROUND_DOWN)

def quantize_quantity(qty, step_size):
    precision = abs(step_size.as_tuple().exponent)
    return qty.quantize(Decimal(f"1e-{precision}"), rounding=ROUND_DOWN)

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

def get_trade_filters_old(symbol):
    """Fetch filters and return step_size, min_qty, min_notional as Decimals."""
    filters = get_symbol_filters(symbol)
    step_size = Decimal(get_filter_value(filters, "LOT_SIZE", "stepSize"))
    min_qty = Decimal(get_filter_value(filters, "LOT_SIZE", "minQty"))
    min_notional = get_min_notional(filters)
    logging.info(f"[FILTERS] step_size={step_size}, min_qty={min_qty}, min_notional={min_notional}")
    return step_size, min_qty, min_notional

def get_trade_filters_new_failed(symbol):
    info = client.get_symbol_info(symbol)
    step_size = Decimal(next(f["stepSize"] for f in info["filters"] if f["filterType"] == "LOT_SIZE"))
    min_qty = Decimal(next(f["minQty"] for f in info["filters"] if f["filterType"] == "LOT_SIZE"))
    min_notional = Decimal(next(f["minNotional"] for f in info["filters"] if f["filterType"] == "MIN_NOTIONAL"))
    return step_size, min_qty, min_notional
def get_trade_filters(symbol):
    info = client.get_symbol_info(symbol)  # or your HTTP request version
    step_size = min_qty = min_notional = None

    for f in info["filters"]:
        if f["filterType"] == "LOT_SIZE":
            step_size = Decimal(f["stepSize"])
            min_qty = Decimal(f["minQty"])
        elif f["filterType"] == "MIN_NOTIONAL":
            min_notional = Decimal(f["minNotional"])

    # fallback if MIN_NOTIONAL is missing
    if min_notional is None:
        min_notional = Decimal("0.0")

    if min_notional == 0:
        logging.warning(f"No MIN_NOTIONAL filter for {symbol}, proceeding with 0")

    return step_size, min_qty, min_notional


def get_current_price_old(symbol):
    try:
        data = public_get("/api/v3/ticker/price", {"symbol": symbol})
        price = Decimal(str(data["price"]))
        logging.info(f"[PRICE] {symbol}: {price}")
        return price
    except Exception as e:
        logging.exception(f"Failed to fetch current price for {symbol}: {e}")
        raise

def get_current_price_new_failed(symbol):
    ticker = client.get_symbol_ticker(symbol=symbol)
    return Decimal(ticker["price"])

PRICE_CACHE = {}

def get_current_price(symbol):
    now = time.time()
    # cache for 2–3 seconds
    if symbol in PRICE_CACHE and now - PRICE_CACHE[symbol]['ts'] < 3:
        return PRICE_CACHE[symbol]['price']

    ticker = client.get_symbol_ticker(symbol=symbol)
    price = Decimal(ticker["price"])
    PRICE_CACHE[symbol] = {"price": price, "ts": now}
    return price



# -------------------------
# Spot functions
# -------------------------
def get_spot_asset_free_old(asset: str) -> Decimal:
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

def get_spot_asset_free(asset):
    balances = client.get_account()["balances"]
    for b in balances:
        if b["asset"] == asset:
            return Decimal(b["free"])
    return Decimal("0")

def place_spot_market_order_old(symbol: str, side: str, quantity: Decimal):
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

# -----------------------------
# ORDER HELPERS
# -----------------------------
def place_spot_market_order(symbol, side, quantity):
    return client.order_market(symbol=symbol, side=side, quantity=float(quantity))

def place_margin_market_order(symbol, side, quantity=None, quote_order_qty=None, side_effect_type=None):
    return client.create_margin_order(
        symbol=symbol,
        side=side,
        type="MARKET",
        quantity=float(quantity) if quantity else None,
        quoteOrderQty=float(quote_order_qty) if quote_order_qty else None,
        sideEffectType=side_effect_type
    )


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

def margin_loan_old(asset: str, amount: Decimal):
    """
    Borrow asset for cross-margin account.
    POST /sapi/v1/margin/loan
    """
    params = {"asset": asset, "amount": str(amount)}
    return signed_post("/sapi/v1/margin/loan", params)

def margin_repay_old(asset: str, amount: Decimal):
    """
    Repay borrowed asset for cross-margin account.
    POST /sapi/v1/margin/repay
    """
    params = {"asset": asset, "amount": str(amount)}
    return signed_post("/sapi/v1/margin/repay", params)

def place_margin_market_order_old(symbol: str, side: str, quantity: Decimal):
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

BINANCE_API_BASE = "https://api.binance.com"

def _sign_params(params: dict) -> dict:
    query_string = '&'.join([f"{k}={v}" for k, v in params.items()])
    signature = hmac.new(BINANCE_SECRET_KEY.encode(), query_string.encode(), hashlib.sha256).hexdigest()
    params["signature"] = signature
    return params

def get_cross_margin_account():
    """
    Returns full cross-margin account payload (balances, etc.).
    """
    url = f"{BINANCE_API_BASE}/sapi/v1/margin/account"
    ts = get_timestamp_ms()
    params = {"timestamp": ts}
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    signed = _sign_params(params)
    r = requests.get(url, headers=headers, params=signed)
    r.raise_for_status()
    return r.json()

def get_margin_asset_free_old(asset: str) -> Decimal:
    """
    Free balance of an asset in CROSS margin (no spot fallback).
    """
    try:
        data = get_cross_margin_account()
        for b in data.get("userAssets", []):
            if b.get("asset") == asset:
                free = Decimal(b.get("free", "0"))
                logging.info(f"[MARGIN BALANCE] {asset} free={free}")
                return free
        logging.warning(f"[MARGIN BALANCE] {asset} not found; treating free=0")
        return Decimal("0")
    except Exception as e:
        logging.exception(f"Failed to fetch margin balance for {asset}: {e}")
        return Decimal("0")
    
def get_margin_asset_free(asset):
    balances = client.get_margin_account()["userAssets"]
    for b in balances:
        if b["asset"] == asset:
            return Decimal(b["free"])
    return Decimal("0")

def place_margin_market_order(symbol: str, side: str, *, quantity: Decimal | None = None,
                              quote_order_qty: Decimal | None = None,
                              side_effect_type: str = "NO_SIDE_EFFECT") -> dict:
    """
    Places a CROSS-MARGIN MARKET order.
    For BUY with auto-borrow: side_effect_type='MARGIN_BUY'
    For SELL with auto-repay: side_effect_type='AUTO_REPAY'
    Provide either quantity (base amount) OR quote_order_qty (USDT amount).
    """
    url = f"{BINANCE_API_BASE}/sapi/v1/margin/order"
    ts = get_timestamp_ms()
    params = {
        "symbol": symbol,
        "side": side,
        "type": "MARKET",
        "isIsolated": "FALSE",
        "sideEffectType": side_effect_type,
        "timestamp": ts,
    }

    if quantity is not None:
        params["quantity"] = str(quantity)
    elif quote_order_qty is not None:
        params["quoteOrderQty"] = str(quote_order_qty)
    else:
        raise ValueError("Either quantity or quote_order_qty must be provided for margin market order.")

    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    signed = _sign_params(params)
    r = requests.post(url, headers=headers, params=signed)
    # Let caller handle HTTPError to map 418/429/etc consistently
    r.raise_for_status()
    out = r.json()

    # Binance business error (negative code) handling
    if isinstance(out, dict) and "code" in out and isinstance(out["code"], int) and out["code"] < 0:
        raise Exception(f"Binance API error: {out.get('msg', 'Unknown error')}")

    logging.info(f"[BINANCE MARGIN RESPONSE] {out}")
    return out


# -------------------------
# Borrow helper
# -------------------------
def margin_borrow_old(asset, amount):
    """Borrow asset in cross margin before placing an order."""
    logging.info(f"[MARGIN BORROW] {amount} {asset}")
    resp = client.margin_loan(asset=asset, amount=str(amount))
    logging.info(f"[MARGIN BORROW] Response: {resp}")
    return resp
# -----------------------------
# MARGIN HELPERS
# -----------------------------
def margin_borrow(asset, amount):
    try:
        return client.create_margin_loan(asset=asset, amount=str(amount))
    except BinanceAPIException as e:
        logging.error(f"Margin borrow failed: {e}")
        raise

def margin_repay(asset, amount):
    try:
        return client.repay_margin_loan(asset=asset, amount=str(amount))
    except BinanceAPIException as e:
        logging.error(f"Margin repay failed: {e}")
        raise


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


def calc_valid_qty(invest_usdt, price, step_size, min_qty, min_notional):
    qty = invest_usdt / price
    # Round down to the nearest step size
    precision = abs(step_size.as_tuple().exponent)
    qty = qty.quantize(Decimal(f"1e-{precision}"), rounding=ROUND_DOWN)
    
    # Make sure qty >= min_qty
    if qty < min_qty:
        qty = min_qty

    # Make sure qty * price >= min_notional
    if qty * price < min_notional:
        qty = (min_notional / price).quantize(Decimal(f"1e-{precision}"), rounding=ROUND_UP)

    return qty


# ---------------------------------
# Unified trade execution (SPOT + MARGIN)
# ---------------------------------
# -----------------------------
# UNIFIED TRADE EXECUTION
# -----------------------------
def execute_trade(symbol, side, trade_type, buy_pct=None, leverage=None):
    """
    Executes BUY/SELL for SPOT or MARGIN.
    MARGIN BUY can auto-borrow with optional leverage.
    """
    """
    Executes BUY or SELL for SPOT or MARGIN.
    - SPOT uses spot balances + place_spot_market_order
    - MARGIN uses cross-margin balances + place_margin_market_order with sideEffectType
      (MARGIN_BUY on BUY, AUTO_REPAY on SELL)
    """
    # Price & filters
    try:
        price = get_current_price(symbol)
        step_size, min_qty, min_notional = get_trade_filters(symbol)
    except Exception as e:
        logging.exception("Failed to fetch price/filters")
        return {"error": "Price/filters fetch failed"}, 500

    qty = None
    quote_order_qty = None

    if side == "BUY":
        if buy_pct is None:
            raise ValueError("buy_pct required for BUY")
        buy_pct = Decimal(str(buy_pct))
        if not (Decimal("0") < buy_pct <= Decimal("1")):
            buy_pct = DEFAULT_BUY_PCT

        if trade_type == "SPOT":
            usdt_free = get_spot_asset_free("USDT")
            invest_usdt = (usdt_free * buy_pct).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
            qty = quantize_quantity(invest_usdt / price, step_size)
        if trade_type == "SPOT":
            usdt_free = get_spot_asset_free("USDT")
            invest_usdt = usdt_free * buy_pct
            qty = calc_valid_qty(invest_usdt, price, step_size, min_qty, min_notional)
            #resp = place_spot_market_order(symbol, side, qty)

        elif trade_type == "MARGIN":
            m_usdt_free = get_margin_asset_free("USDT")
            invest_usdt = (m_usdt_free * buy_pct).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)

            # Apply leverage if requested
            if leverage and leverage > 1:
                borrow_amount = invest_usdt * (leverage - 1)
                margin_borrow("USDT", borrow_amount)
                invest_usdt += borrow_amount

            quote_order_qty = invest_usdt

        else:
            raise ValueError(f"Unknown trade_type: {trade_type}")

    elif side == "SELL":
        base_asset = symbol.replace("USDT", "")
        if trade_type == "SPOT":
            qty = quantize_quantity(get_spot_asset_free(base_asset), step_size)
        elif trade_type == "MARGIN":
            qty = quantize_quantity(get_margin_asset_free(base_asset), step_size)
        else:
            raise ValueError(f"Unknown trade_type: {trade_type}")

    else:
        raise ValueError(f"Invalid side: {side}")

    # Safeguards
    if quote_order_qty:
        implied_qty = quantize_quantity(quote_order_qty / price, step_size)
        if implied_qty < min_qty or quote_order_qty < min_notional:
            return {"warning": "Trade below min requirements"}, 200
    else:
        if qty < min_qty or (qty * price) < min_notional:
            return {"warning": "Trade below min requirements"}, 200

    # Execute order
    try:
        if trade_type == "SPOT":
            resp = place_spot_market_order(symbol, side, qty)
        else:
            if side == "BUY":
                resp = place_margin_market_order(symbol, side, quote_order_qty=quote_order_qty, side_effect_type="MARGIN_BUY")
            else:
                resp = place_margin_market_order(symbol, side, quantity=qty, side_effect_type="AUTO_REPAY")
    except requests.exceptions.HTTPError as e:
        return {"error": str(e)}, 500

    logging.info(f"Executed {trade_type} {side} {symbol} ~{price}")
    return {"status": f"{trade_type.lower()}_{side.lower()}_executed", "order": resp}, 200

def execute_trade_old(symbol, side, trade_type, buy_pct=None, leverage=1):
    """
    Executes BUY or SELL for SPOT or MARGIN.
    - SPOT uses spot balances + place_spot_market_order
    - MARGIN uses cross-margin balances + place_margin_market_order with sideEffectType
      (MARGIN_BUY on BUY, AUTO_REPAY on SELL)
    """
    # Price & filters
    try:
        price = get_current_price(symbol)
        step_size, min_qty, min_notional = get_trade_filters(symbol)
    except Exception as e:
        logging.exception("Failed to fetch price/filters")
        return jsonify({"error": "Price/filters fetch failed"}), 500

    qty = None
    quote_order_qty = None

    if side == "BUY":
        if buy_pct is None:
            raise ValueError("buy_pct required for BUY")

        if trade_type == "SPOT":
            usdt_free = get_spot_asset_free("USDT")
            invest_usdt = (usdt_free * buy_pct).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
            qty = quantize_quantity(invest_usdt / price, step_size)

        elif trade_type == "MARGIN":
            m_usdt_free = get_margin_asset_free("USDT")
            target_invest = (m_usdt_free * leverage * buy_pct).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
            borrow_needed = target_invest - m_usdt_free

            if borrow_needed > 0:
                margin_borrow("USDT", borrow_needed)

            quote_order_qty = target_invest

        else:
            raise ValueError(f"Unknown trade_type: {trade_type}")

    elif side == "SELL":
        base_asset = symbol.replace("USDT", "")
        if trade_type == "SPOT":
            base_free = get_spot_asset_free(base_asset)
            qty = quantize_quantity(base_free, step_size)

        elif trade_type == "MARGIN":
            m_base_free = get_margin_asset_free(base_asset)
            qty = quantize_quantity(m_base_free, step_size)

        else:
            raise ValueError(f"Unknown trade_type: {trade_type}")

    # Safeguards
    if quote_order_qty is not None:
        implied_qty = quantize_quantity((quote_order_qty / price) if price > 0 else Decimal("0"), step_size)
        if implied_qty < min_qty or quote_order_qty < min_notional:
            return {"warning": "Trade size below Binance minimums"}, 200
    else:
        if qty is None or qty < min_qty or (qty * price) < min_notional:
            return {"warning": "Trade size below Binance minimums"}, 200

    # Execute
    try:
        if trade_type == "SPOT":
            resp = place_spot_market_order(symbol, side, qty)
        else:
            if side == "BUY":
                resp = place_margin_market_order(
                    symbol, side, quantity=None, quote_order_qty=quote_order_qty, side_effect_type="MARGIN_BUY"
                )
            else:
                resp = place_margin_market_order(
                    symbol, side, quantity=qty, quote_order_qty=None, side_effect_type="AUTO_REPAY"
                )
    except requests.exceptions.HTTPError as e:
        err_str = str(e).lower()
        if "418" in err_str or "teapot" in err_str:
            return {"error": "Binance rate limit hit (418)"}, 429
        if "429" in err_str:
            return {"error": "Binance request limit hit (429)"}, 429
        if "notional" in err_str:
            return {"error": "Trade rejected: below Binance min_notional"}, 400
        raise

    return {"status": f"{trade_type.lower()}_{side.lower()}_executed", "order": resp}, 200


# -------------------------
# Webhook endpoint
# -------------------------
@app.route(WEBHOOK_REQUEST_PATH, methods=['POST'])
def webhook():
    logging.info("=====================start=====================")
    
    # Validate JSON & timestamp & secret
    data, error_response = validate_json()
    if not data:
        return error_response
    valid_ts, ts, error_response = validate_timestamp(data)
    if not valid_ts:
        return error_response
    valid_secret, error_response = validate_secret(data)
    if not valid_secret:
        return error_response

    # Log payload without secret
    data_for_log = {k: v for k, v in data.items() if k != SECRET_FIELD}
    logging.info(f"[WEBHOOK] Received payload (no {SECRET_FIELD}): {data_for_log}")

    try:
        action = data.get("action", "").strip().upper()
        symbol = data.get("symbol", "").strip().upper()
        trade_type = data.get("type", "SPOT").strip().upper()
        buy_pct = Decimal(str(data.get("buy_pct", DEFAULT_BUY_PCT)))
        leverage = Decimal(str(data.get("leverage", 1)))  # Default 1x if not provided
    except Exception as e:
        logging.exception("Failed to parse fields")
        return jsonify({"error": "Invalid fields"}), 400

    logging.info(f"[PARSE] action={action}, symbol={symbol}, trade_type={trade_type}, buy_pct={buy_pct}, leverage={leverage}")

    if action not in {"BUY", "BUY_BTC_SMALL", "SELL"}:
        logging.error(f"Invalid action: {action}")
        return jsonify({"error": "Invalid action"}), 400
    if symbol not in ALLOWED_SYMBOLS:
        logging.error(f"Symbol not allowed: {symbol}")
        return jsonify({"error": "Symbol not allowed"}), 400

    side = "BUY" if action in {"BUY", "BUY_BTC_SMALL"} else "SELL"

    try:
        result, status_code = execute_trade(symbol, side, trade_type, buy_pct if side == "BUY" else None, leverage)
        return jsonify(result), status_code
    except Exception as e:
        logging.exception("Trade execution failed")
        return jsonify({"error": f"Trade execution failed: {str(e)}"}), 500


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
