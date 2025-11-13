from flask import Flask, jsonify
import time
import logging
from decimal import Decimal

# Redis and WebSocket price cache and background_cache
from binance_data import (
    init_redis,
    init_client,
    start_ws_price_cache,
    start_background_cache,
    refresh_balances_for_assets,
    log_order_to_cache,
    safe_log_webhook_error,
)

from validation import (
    validate_order_qty,
    run_webhook_validations,
    validate_and_normalize_trade_fields,
)

from routes import routes
from utils import (
    log_webhook_payload,
    log_webhook_delimiter,
    log_parsed_payload,
    split_symbol,
    quantize_quantity,
    sanitize_filters,
)

from exchange import (
    get_current_price,
    get_symbol_filters,
    get_balances,
    resolve_trade_amount,
    place_order_with_handling,
    place_spot_market_order,
)

# -------------------------
# Configuration
# -------------------------
from config._settings import (
    ALLOWED_TRADE_TYPES,
    ALLOWED_SYMBOLS,
    SECRET_FIELD,
    WEBHOOK_REQUEST_PATH,
    BINANCE_API_KEY,
    BINANCE_SECRET_KEY,
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
        filters = get_symbol_filters(symbol)
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
        
        balances = get_balances() or {}
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
            symbol=symbol,
            side=side,
            free_balance=free_balance,
            amt=amt,
            pct=pct,
            price=price,
            amt_in_crypto=amt_in_crypto,
            amt_in_funds=amt_in_funds,
        )
        if error_msg:
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
