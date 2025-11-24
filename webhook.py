from flask import Blueprint, jsonify
import time
import logging

from binance_data import (
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

from config._settings import (
    ALLOWED_TRADE_TYPES,
    ALLOWED_SYMBOLS,
    SECRET_FIELD,
    WEBHOOK_REQUEST_PATH,
)

webhook = Blueprint("webhook", __name__)

# -------------------------
# Webhook endpoint
# -------------------------
@webhook.route(WEBHOOK_REQUEST_PATH, methods=['POST'])
def webhook_handler():
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

            buy_quote_pct_raw = data.get("buy_quote_pct")
            buy_quote_amount_raw = data.get("buy_quote_amount")
            buy_base_amount_raw = data.get("buy_base_amount")

            sell_base_pct_raw = data.get("sell_base_pct")
            sell_base_amount_raw = data.get("sell_base_amount")
            sell_quote_amount_raw = data.get("sell_quote_amount")

            trade_type = data.get("type", "SPOT").strip().upper()
        except Exception:
            logging.exception("Failed to extract fields")
            message = "Failed to extract fields from webhook payload"
            safe_log_webhook_error(symbol=None, side=None, message=message)
            return jsonify({"error": message}), 400

        log_parsed_payload(
            action,
            symbol,
            buy_quote_pct_raw,
            buy_quote_amount_raw,
            buy_base_amount_raw,
            sell_base_pct_raw,
            sell_base_amount_raw,
            sell_quote_amount_raw,
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
        pct, amt, amount_is_base, amount_is_quote, error_response = validate_and_normalize_trade_fields(
            action, is_buy,
            buy_quote_pct_raw, buy_quote_amount_raw, buy_base_amount_raw,
            sell_base_pct_raw, sell_base_amount_raw, sell_quote_amount_raw
        )
        if error_response:
            message = "Invalid trade field."
            logging.error(message)
            safe_log_webhook_error(symbol, action, message)
            return error_response
        if not amount_is_base and not amount_is_quote:
            message = "Ambiguous amount source: neither base nor quote amount detected."
            logging.error(message)
            safe_log_webhook_error(symbol, action, message)
            return jsonify({"error": message}), 400
        if amount_is_base and amount_is_quote:
            message = "Invalid field combination: amount cannot be both base and quote."
            logging.error(message)
            safe_log_webhook_error(symbol, action, message)
            return jsonify({"error": message}), 400

        result, status_code = execute_trade(
            symbol=symbol,
            side="BUY" if is_buy else "SELL",
            pct=pct,
            amt=amt,
            amount_is_base=amount_is_base,
            amount_is_quote=amount_is_quote,
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