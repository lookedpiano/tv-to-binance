from flask import Flask, request, jsonify
import hmac
import requests
import time
import logging
from decimal import Decimal

# binance-connector imports (official SDK)
from binance.spot import Spot as Client
from binance.error import ClientError, ServerError

# Redis and WebSocket price cache and background_cache
from binance_data import (
    init_redis,
    init_client,
    start_ws_price_cache,
    start_background_cache,
    get_cached_price,
    get_cached_balances,
    get_cached_symbol_filters,
    refresh_balances_for_assets,
    log_order_to_cache,
)

from routes import routes
from utils import (
    load_ip_file,
    log_webhook_payload,
    log_webhook_delimiter,
    log_parsed_payload,
    split_symbol,
    quantize_quantity,
    quantize_down,
    get_filter_value,
    sanitize_filters,
)


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
    BINANCE_API_KEY,
    BINANCE_SECRET_KEY,
    WEBHOOK_SECRET,
    PORT,
    REDIS_URL,
)

# -------------------------
# Logging configuration
# -------------------------
logging.basicConfig(
    level=logging.INFO,
    format='[%(levelname)s] %(message)s'
)

app = Flask(__name__)

# Register routes
app.register_blueprint(routes)


# -------------------------
# CLIENT INIT
# -------------------------
client = init_client(BINANCE_API_KEY, BINANCE_SECRET_KEY)

# -------------------------
# REDIS + WS INIT
# -------------------------
try:
    init_redis(REDIS_URL)
    start_ws_price_cache(ALLOWED_SYMBOLS)
    start_background_cache(ALLOWED_SYMBOLS)
    logging.info("[INIT] Background caches initialized successfully.")
except Exception as e:
    logging.exception(f"[INIT] Failed to initialize background caches: {e}")

# -----------------------
# Validation functions
# -----------------------
def run_webhook_validations():
    try:
        valid_ip, error_response = validate_outbound_ip_address()
        if not valid_ip:
            safe_log_webhook_error(symbol=None, side=None, message="Outbound IP not allowed")
            return None, error_response

        data, error_response = validate_json()
        if not data:
            safe_log_webhook_error(symbol=None, side=None, message="Invalid JSON payload")
            return None, error_response

        valid_fields, error_response = validate_fields(data)
        if not valid_fields:
            symbol = (
                str(data.get("symbol", "")).strip().upper()
                if isinstance(data, dict)
                else None
            )
            action = (
                str(data.get("action", "")).strip().upper()
                if isinstance(data, dict)
                else None
            )
            safe_log_webhook_error(symbol, action, message="Invalid or missing fields in payload")
            return None, error_response

        valid_secret, error_response = validate_secret(data)
        if not valid_secret:
            symbol = (
                str(data.get("symbol", "")).strip().upper()
                if isinstance(data, dict)
                else None
            )
            action = (
                str(data.get("action", "")).strip().upper()
                if isinstance(data, dict)
                else None
            )
            safe_log_webhook_error(symbol, action, message="Invalid or missing secret")
            return None, error_response

        return data, None

    except Exception as e:
        safe_log_webhook_error(symbol=None, side=None, message=f"Validation exception: {e}")
        logging.exception("[VALIDATION] Unexpected error during webhook validation")
        return None, (jsonify({"error": "Unexpected validation error"}), 500)

def validate_json():
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
    secret_from_request = data.get(SECRET_FIELD)
    if not secret_from_request:
        logging.warning("[SECURITY] Missing secret field")
        return False, (jsonify({"error": "Unauthorized"}), 401)

    if not hmac.compare_digest(secret_from_request, WEBHOOK_SECRET):
        logging.warning("[SECURITY] Unauthorized attempt (invalid secret)")
        return False, (jsonify({"error": "Unauthorized"}), 401)

    return True, None

def validate_order_qty(
    symbol: str,
    qty: Decimal,
    price: Decimal | None,
    min_qty: Decimal,
    min_notional: Decimal,
    side: str = "?",
) -> tuple[bool, dict, int]:
    """
    Validate order quantity and notional against exchange filters.
    """
    logging.info(f"[SAFEGUARDS] Validate order qty for {symbol}: {qty}")

    try:
        if qty <= Decimal("0"):
            message = "Trade qty is zero or negative after rounding. Aborting."
            logging.warning(message)
            log_order_to_cache(symbol, side, qty, price, status="error", message=message)
            return False, {"warning": message}, 200

        if qty < min_qty:
            message = f"Trade qty {qty} is below min_qty {min_qty}. Aborting."
            logging.warning(message)
            log_order_to_cache(symbol, side, qty, price, status="error", message=message)
            return False, {"warning": message}, 200

        if (qty * price) < min_notional:
            message = f"Trade notional {qty * price} is below min_notional {min_notional}. Aborting."
            logging.warning(message)
            log_order_to_cache(symbol, side, qty, price, status="error", message=message)
            return False, {"warning": message}, 200

    except Exception as e:
        logging.warning(f"[ORDER LOG] Failed to log validation error: {e}")

    # Successfully validated
    return True, {}, 200

def validate_and_normalize_trade_fields(
    action: str,
    is_buy: bool,
    buy_funds_pct_raw,
    buy_funds_amount_raw,
    buy_crypto_amount_raw,
    sell_crypto_pct_raw,
    sell_crypto_amount_raw,
    sell_funds_amount_raw
):
    """
    Validates and normalizes trade fields for BUY or SELL.

    Rules:
    - BUY: only buy_* fields are considered.
    - SELL: only sell_* fields are considered.
    - Exactly one of the relevant fields must be provided.
    - Pct must be in (0, 1].
    - Amount must be positive Decimal.

    Returns:
        pct, amt, amt_in_crypto, amt_in_funds, error_response
    """

    # --- Step 1: pick the relevant group ---
    if is_buy:
        relevant = {
            "buy_funds_pct": buy_funds_pct_raw,
            "buy_funds_amount": buy_funds_amount_raw,
            "buy_crypto_amount": buy_crypto_amount_raw,
        }
    else:
        relevant = {
            "sell_crypto_pct": sell_crypto_pct_raw,
            "sell_crypto_amount": sell_crypto_amount_raw,
            "sell_funds_amount": sell_funds_amount_raw,
        }

    # --- Step 2: check how many were provided ---
    non_none = {k: v for k, v in relevant.items() if v is not None}

    if len(non_none) == 0:
        logging.error(f"No trade size field provided for {action}")
        return None, None, False, False, (
            jsonify({"error": f"Please provide one of: {', '.join(relevant.keys())}."}), 400
        )

    if len(non_none) > 1:
        logging.error(f"Multiple trade size fields provided for {action}: {list(non_none.keys())}")
        return None, None, False, False, (
            jsonify({"error": f"Please provide only one of: {', '.join(relevant.keys())}."}), 400
        )

    # --- Step 3: normalize ---
    field_name, raw_value = next(iter(non_none.items()))
    logging.info(f"[FIELDS] Using {field_name}={raw_value}")

    # Percentage case
    if "pct" in field_name:
        try:
            pct = Decimal(str(raw_value))
            if not (Decimal("0") < pct <= Decimal("1")):
                raise ValueError
            # For BUY pct → funds-based, for SELL pct → crypto-based
            amt_in_funds = is_buy
            amt_in_crypto = not is_buy
            return pct, None, amt_in_crypto, amt_in_funds, None
        except Exception:
            return None, None, False, False, (
                jsonify({"error": f"{field_name} must be a number between 0 and 1."}), 400
            )

    # Amount case
    try:
        amt = Decimal(str(raw_value))
        if amt <= 0:
            raise ValueError
    except Exception:
        return None, None, False, False, (
            jsonify({"error": f"{field_name} must be a positive number."}), 400
        )

    # --- Step 4: flag mapping ---
    amt_in_crypto = field_name in ("buy_crypto_amount", "sell_crypto_amount")
    amt_in_funds = field_name in ("buy_funds_amount", "sell_funds_amount")

    return None, amt, amt_in_crypto, amt_in_funds, None

def validate_fields(data: dict):
    unknown_fields = set(data.keys()) - ALLOWED_FIELDS
    if unknown_fields:
        logging.error(f"Unknown fields in payload: {unknown_fields}")
        return False, (jsonify({"error": f"Unknown fields: {list(unknown_fields)}"}), 400)

    missing_fields = REQUIRED_FIELDS - set(data.keys())
    if missing_fields:
        logging.error(f"Missing required fields: {missing_fields}")
        return False, (jsonify({"error": f"Missing required fields: {list(missing_fields)}"}), 400)

    return True, None

def validate_outbound_ip_address() -> tuple[bool, tuple | None]:
    try:
        current_ip = requests.get("https://api.ipify.org", timeout=21).text.strip()
        logging.info(f"[OUTBOUND_IP] Validate current outbound IP for Binance calls: {current_ip}")

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
# Exchange helpers (connector)
# -------------------------
def get_symbol_filters(symbol: str):
    """
    Fetch symbol filters (lot size, min notional, etc.) for a given symbol.
    Using exchange_info(symbol=...) → info["symbols"][0]["filters"]
    """
    try:
        info = client.exchange_info(symbol=symbol)
        if not info or "symbols" not in info or not info["symbols"]:
            return []
        filters = info["symbols"][0].get("filters", [])
        return filters
    except ClientError as e:
        logging.error(f"Binance API error while fetching filters for {symbol}: {e.error_message}")
        return []
    except Exception as e:
        logging.exception(f"Failed to fetch exchangeInfo for {symbol}: {e}")
        return []

def get_min_notional(filters):
    min_notional = next((f['minNotional'] for f in filters if f['filterType'] == 'MIN_NOTIONAL'), None)
    if min_notional:
        return Decimal(min_notional)
    notional_filter = next((f for f in filters if f['filterType'] == 'NOTIONAL'), None)
    if not notional_filter:
        return Decimal('0.0')
    return Decimal(notional_filter['minNotional'])

def get_trade_filters(symbol):
    filters = get_symbol_filters(symbol)
    if not filters:
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

# -------- Price (connector) --------
def get_current_price(symbol: str):
    """
    Return current price using the WebSocket cache first.
    Fallback to REST once if cache is cold (e.g., right after restart).
    """
    # 1) Try cached price (no REST hit)
    price = get_cached_price(symbol)
    if price is not None:
        logging.info(f"[PRICE:CACHE] {symbol}: {price}")
        return price

    # 2) Fallback: single REST call (rare; only if cache hasn't seen this symbol yet)
    try:
        data = client.ticker_price(symbol)
        price = Decimal(data["price"])
        logging.info(f"[PRICE:REST] {symbol}: {price}")
        return price
    except ClientError as e:
        logging.error(f"[PRICE:REST] ClientError while fetching price for {symbol}: {e.error_message}")
        if e.status_code in (418, 429) or e.error_code in (-1003,):
            logging.warning(f"Rate limit or temp block for {symbol}: <{e.error_message}>")
            return None
        return None
    except Exception as e:
        logging.exception(f"[PRICE:REST] Unexpected error fetching price for {symbol}: {e}")
        return None

# -------------------------
# Spot functions (connector)
# -------------------------
def get_spot_asset_free(asset: str) -> Decimal:
    """
    Return free balance for asset from spot account as Decimal.
    """
    try:
        account_info = client.account()
        balances = account_info.get("balances", [])
        for b in balances:
            if b.get("asset") == asset:
                free = Decimal(str(b.get("free", "0")))
                logging.info(f"[SPOT BALANCE] {asset} free={free}")
                return free
        return Decimal("0")
    except ClientError as e:
        logging.error(f"Binance API error while fetching {asset} balance: {e.error_message}")
        raise
    except Exception as e:
        logging.exception(f"Failed to fetch spot asset balance for {asset}: {e}")
        raise

def place_spot_market_order(symbol, side, quantity):
    """
    Place a MARKET order. Quantity must be base asset amount.
    """
    # use str(quantity) to avoid float precision
    return client.new_order(symbol=symbol, side=side, type="MARKET", quantity=str(quantity))

def resolve_trade_amount(
    free_balance: Decimal,
    amt: Decimal | None,
    pct: Decimal | None,
    side: str,
    price: Decimal | None = None,
    amt_in_crypto: bool = False,
    amt_in_funds: bool = False,
) -> tuple[Decimal | None, str | None]:
    """
    Resolves the target trade amount based on the provided parameters and context.

    - BUY:
        * amt_in_crypto=True  → buy this many base units (e.g. 5 ADA)
        * amt_in_funds=True   → spend this much quote (e.g. 10 USDT)
    - SELL:
        * amt_in_crypto=True  → sell this many base units (e.g. 0.001 BTC)
        * amt_in_funds=True   → sell enough base to get this much quote (e.g. 6 USDT)

    Returns: (target_amount_in_relevant_units, error_msg)
    """

    # --- Explicit amount path ---
    if amt is not None:
        if side == "BUY":
            if amt_in_crypto:
                # e.g. buy 5 ADA
                target = amt
                logging.info(f"[INVEST:BUY-CRYPTO-AMOUNT] Buying {target} base units")
            elif amt_in_funds:
                # e.g. spend 10 USDT to buy base
                target = amt  # still in quote, handled later in execute_trade()
                logging.info(f"[INVEST:BUY-FUNDS-AMOUNT] Spending {target} quote")
            else:
                target = amt  # fallback
        else:  # SELL
            if amt_in_crypto:
                # e.g. sell 0.001 BTC
                if amt > free_balance:
                    msg = f"Balance insufficient: requested={amt}, available={free_balance}"
                    logging.warning(f"[INVEST:SELL-CRYPTO-AMOUNT] {msg}")
                    return None, msg
                target = amt
                logging.info(f"[INVEST:SELL-CRYPTO-AMOUNT] Selling {target} base units")
            elif amt_in_funds:
                # e.g. sell BTC worth 6 USDT
                if not price:
                    return None, "Missing price for funds-based sell"
                crypto_equiv = amt / price
                if crypto_equiv > free_balance:
                    msg = f"Balance insufficient: requested={crypto_equiv}, available={free_balance}"
                    logging.warning(f"[INVEST:SELL-FUNDS-AMOUNT] {msg}")
                    return None, msg
                target = crypto_equiv
                logging.info(f"[INVEST:SELL-FUNDS-AMOUNT] Selling {crypto_equiv} base (≈{amt} quote)")
            else:
                target = amt  # fallback

        return target, None

    # --- Percentage path ---
    if pct is not None:
        resolved_amt = quantize_down(free_balance * pct, "0.00000001")
        logging.info(f"[INVEST:{side}-PERCENTAGE] Using pct={float(pct)}, resolved_amt={resolved_amt}")
        return resolved_amt, None

    return None, "Neither amount nor percentage provided"

def place_order_with_handling(symbol: str, side: str, qty: Decimal, price: Decimal, place_order_fn):
    """
    Place an order safely with unified exception handling and logging.
    """
    try:
        resp = place_order_fn(symbol, side, qty)
    except ClientError as e:
        msg = e.error_message.lower() if e.error_message else ""
        code = e.error_code
        status = e.status_code
        if status in (418, 429) or code in (-1003,):
            logging.error(f"Binance rate limit hit ({status}/{code}): {e.error_message}")
            return {"error": f"Binance request limit hit ({status})"}, 429
        if "notional" in msg or code in (-1013,):
            logging.error("Trade rejected: below Binance min_notional")
            return {"error": "Trade rejected: below Binance min_notional"}, 400
        logging.exception(f"Order placement failed: {e}")
        return {"error": f"Order failed: {e.error_message}"}, 400
    except ServerError as e:
        logging.error(f"Binance server error: {e}")
        return {"error": "Binance server error"}, 502
    except Exception as e:
        logging.exception(f"Unexpected order error: {e}")
        return {"error": f"Unexpected order error: {str(e)}"}, 500

    logging.info(f"[ORDER] {side} successfully executed: qty={qty} {symbol} at price={price}")
    return {"status": f"spot_{side.lower()}_executed", "order": resp}, 200

def safe_log_webhook_error(symbol, side, message):
    """Helper to safely log webhook-level failures before execute_trade() runs."""
    try:
        log_order_to_cache(
            symbol or "?",
            side or "?",
            qty=None,
            price=None,
            status="error",
            message=message
        )
    except Exception as e:
        logging.warning(f"[ORDER LOG] Failed to log webhook-level error: {e}")

# ---------------------------------
# Unified trade execution
# ---------------------------------
def execute_trade(
    symbol: str,
    side: str,
    pct=None,
    amt=None,
    amt_in_crypto=False,
    amt_in_funds=False,
    trade_type="SPOT",
    place_order_fn=None,
):
    """
    Unified trade executor for SPOT; handles buy/sell, quantity math, filter validation, and order placement.
    """
    try:
        logging.info(f"[EXECUTE] side={side}, pct={pct}, amt={amt}, amt_in_crypto={amt_in_crypto}, amt_in_funds={amt_in_funds}")

        # === 1. Price retrieval (with one retry) ===
        price = get_current_price(symbol)
        if price is None:
            logging.info(f"[EXECUTE] Retrying price fetch for {symbol} in 3s...")
            time.sleep(3)
            price = get_current_price(symbol)
        if price is None:
            message = f"No price available for {symbol}. Aborting trade."
            logging.warning(f"[EXECUTE] {message}")
            try:
                log_order_to_cache(symbol, side or "?", "?", "?",status="error", message=message)
            except Exception as e:
                logging.warning(f"[ORDER LOG] Failed to log missing price error: {e}")
            return {"error": message}, 200

        # === 2. Fetch filters ===
        filters = get_cached_symbol_filters(symbol)
        if not filters:
            message = f"Filters unavailable for {symbol}"
            logging.warning(f"[EXECUTE] {message}")
            try:
                log_order_to_cache(symbol, side or "?", "?", price,status="error", message=message)
            except Exception as e:
                logging.warning(f"[ORDER LOG] Failed to log missing filters error: {e}")
            return {"error": message}, 200

        filters = sanitize_filters(filters)

        step_size = Decimal(filters.get("step_size", "0"))
        min_qty = Decimal(filters.get("min_qty", "0"))
        min_notional = Decimal(filters.get("min_notional", "0"))
        if not all([step_size, min_qty, min_notional]):
            message = (
                f"Incomplete filters for {symbol}: "
                f"step_size={step_size}, min_qty={min_qty}, min_notional={min_notional}"
            )
            logging.warning(f"[EXECUTE] {message}")
            try:
                log_order_to_cache(symbol, side or "?", "?", price,status="error", message=message)
            except Exception as e:
                logging.warning(f"[ORDER LOG] Failed to log incomplete filters error: {e}")
            return {"error": message}, 200

        # === 3. Determine assets ===
        try:
            base_asset, quote_asset = split_symbol(symbol)
        except ValueError as e:
            message = f"Failed to parse base/quote assets for {symbol}: {e}"
            logging.error(f"[EXECUTE] {message}")
            try:
                log_order_to_cache(symbol, side or "?", "?", price,status="error", message=message)
            except Exception as log_err:
                logging.warning(f"[ORDER LOG] Failed to log symbol-parse error: {log_err}")
            return {"error": message}, 400

        # === 4. Determine balance and target amount ===
        if side == "BUY":
            balance_asset = quote_asset
        elif side == "SELL":
            balance_asset = base_asset
        else:
            message = f"Unknown side {side}. Must be BUY or SELL."
            logging.error(f"[EXECUTE] {message}")
            try:
                log_order_to_cache(symbol, side or "?", "?", price,status="error", message=message)
            except Exception as e:
                logging.warning(f"[ORDER LOG] Failed to log invalid side error: {e}")
            return {"error": message}, 400
        
        balances = get_cached_balances() or {}
        free_balance = balances.get(balance_asset, Decimal("0"))
        if free_balance <= 0:
            message = f"No available {balance_asset} balance to {side.lower()}."
            logging.warning(f"[EXECUTE] {message}")
            try:
                log_order_to_cache(symbol, side, "?", price,status="error", message=message)
            except Exception as e:
                logging.warning(f"[ORDER LOG] Failed to log balance error: {e}")
            return {"warning": message}, 200

        # Resolve amount
        target_amount, error_msg = resolve_trade_amount(
            free_balance,
            amt,
            pct,
            side,
            price,
            amt_in_crypto,
            amt_in_funds
        )
        if error_msg:
            logging.warning(f"[EXECUTE] {error_msg}")
            try:
                log_order_to_cache(symbol, side, "?", price,status="error", message=error_msg)
            except Exception as e:
                logging.warning(f"[ORDER LOG] Failed to log resolve_trade_amount error: {e}")
            return {"error": error_msg}, 200

        # === 5. Compute quantity ===
        # target_amount here may refer to base or quote, depending on flags
        if side == "BUY":
            if amt_in_crypto:
                # User specified base asset directly, e.g. buy 1.2 ETH
                raw_qty = amt
                notional = raw_qty * price
                logging.info(f"[BUY:CRYPTO-AMOUNT] qty={raw_qty} ({notional:.2f} quote value)")
            else:
                # Normal path: buy_funds_amount or buy_funds_pct (in quote)
                raw_qty = target_amount / price
                notional = target_amount
                logging.info(f"[BUY:FUNDS-{('PCT' if pct else 'AMT')}] notional≈{notional:.2f}, qty={raw_qty}")

        elif side == "SELL":
            if amt_in_funds:
                # User specified desired quote amount, e.g. sell BTC worth 100 USDT
                raw_qty = amt / price
                notional = amt
                logging.info(f"[SELL:FUNDS-AMOUNT] notional≈{notional:.2f}, qty={raw_qty}")
            else:
                # Normal path: sell_crypto_amount or sell_crypto_pct (in base)
                raw_qty = target_amount
                notional = raw_qty * price
                logging.info(f"[SELL:CRYPTO-{('PCT' if pct else 'AMT')}] qty={raw_qty}, notional≈{notional:.2f}")
        else:
            return {"error": f"Unknown side {side}"}, 400

        qty = quantize_quantity(raw_qty, step_size)
        notional = qty * price

        # === 6. Log trade intent ===
        action_label = "BUY" if side == "BUY" else "SELL"
        logging.info(f"[EXECUTE {action_label}] {symbol}: qty={qty}, price={price}, notional≈{notional:.2f}")
        logging.debug(f"[DETAILS] step_size={step_size}, min_qty={min_qty}, min_notional={min_notional}")

        # === 7. Validate filters ===
        is_valid, resp_dict, http_status = validate_order_qty(symbol, qty, price, min_qty, min_notional, side)
        if not is_valid:
            return resp_dict, http_status

        # === 8. Place order ===
        result, order_http_status = place_order_with_handling(symbol, side, qty, price, place_order_fn)

        # === 9. Determine outcome and refresh balances if trade succeeded ===
        if order_http_status == 200 and result and "error" not in result:
            order_status = "success"
            message = f"Order executed successfully ({symbol} {side})"
            try:
                refresh_balances_for_assets(client, [base_asset, quote_asset])
            except Exception as e:
                logging.warning(f"[CACHE] Post-trade balance refresh failed: {e}")
        else:
            order_status = "error"
            message = result.get("error", "Unknown failure") if isinstance(result, dict) else str(result)

        # === 10. Log order attempt ===
        try:
            log_order_to_cache(symbol, side, qty, price, order_status, message)
        except Exception as e:
            logging.warning(f"[ORDER LOG] Failed to log order: {e}")

        return result, order_http_status

    except Exception as e:
        logging.exception(f"[EXECUTE] Trade execution failed for {symbol}")
        return {"error": f"Trade execution failed: {str(e)}"}, 500

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

        log_webhook_payload(data, SECRET_FIELD)

        try:
            action = data.get("action", "").strip().upper()
            symbol = data.get("symbol", "").strip().upper()
            buy_funds_pct_raw = data.get("buy_funds_pct")
            buy_funds_amount_raw = data.get("buy_funds_amount")
            buy_crypto_amount_raw = data.get("buy_crypto_amount")
            sell_crypto_pct_raw = data.get("sell_crypto_pct")
            sell_crypto_amount_raw = data.get("sell_crypto_amount")
            sell_funds_amount_raw = data.get("sell_funds_amount")
            trade_type = data.get("type", "SPOT").strip().upper()
        except Exception:
            logging.exception("Failed to extract fields")
            message = "Failed to extract fields from webhook payload"
            safe_log_webhook_error(symbol=None, side=None, message=message)
            return jsonify({"error": message}), 400

        log_parsed_payload(
            action,
            symbol,
            buy_funds_pct_raw,
            buy_funds_amount_raw,
            buy_crypto_amount_raw,
            sell_crypto_pct_raw,
            sell_crypto_amount_raw,
            sell_funds_amount_raw,
            trade_type
        )

        resp = detect_tradingview_placeholder(action)
        if resp:
            return resp

        if action not in {"BUY", "SELL"}:
            message = f"Invalid action: {action}"
            logging.error(message)
            safe_log_webhook_error(symbol, action, message)
            return jsonify({"error": message}), 400
        if trade_type not in ALLOWED_TRADE_TYPES:
            message = f"Invalid trade_type: {trade_type}"
            logging.error(message)
            safe_log_webhook_error(symbol, action, message)
            return jsonify({"error": message}), 400
        if symbol not in ALLOWED_SYMBOLS:
            message = f"Symbol not allowed: {symbol}"
            logging.error(message)
            safe_log_webhook_error(symbol, action, message)
            return jsonify({"error": message}), 400

        is_buy = action == "BUY"
        pct, amt, amt_in_crypto, amt_in_funds, error_response = validate_and_normalize_trade_fields(
            action, is_buy,
            buy_funds_pct_raw, buy_funds_amount_raw, buy_crypto_amount_raw,
            sell_crypto_pct_raw, sell_crypto_amount_raw, sell_funds_amount_raw
        )
        if error_response:
            message = error_response[0].get("error", "Invalid trade field")
            safe_log_webhook_error(symbol, action, message)
            return error_response

        result, status_code = execute_trade(
            symbol=symbol,
            side="BUY" if is_buy else "SELL",
            pct=pct,
            amt=amt,
            amt_in_crypto=amt_in_crypto,
            amt_in_funds=amt_in_funds,
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
    if action == "{{STRATEGY.ORDER.ACTION}}":
        logging.warning("TradingView placeholder received instead of explicit action.")
        logging.warning(
            "Did you accidentally paste {{strategy.order.action}} instead of letting "
            "TradingView expand it? Use BUY or SELL instead..."
        )
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
