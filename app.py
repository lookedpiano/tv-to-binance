from flask import Flask, jsonify
import time
import logging

# Redis and WebSocket price cache and background_cache
from binance_data import (
    init_redis,
    init_client,
    start_ws_price_cache,
    start_background_cache,
    safe_log_webhook_error,
)

from validation import (
    run_webhook_validations,
    validate_and_normalize_trade_fields,
)

from utils import (
    log_webhook_payload,
    log_webhook_delimiter,
    log_parsed_payload,
)

from exchange import (
    place_spot_market_order,
)

from trade import (
    execute_trade,
)

from routes import routes

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
