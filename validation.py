from flask import request, jsonify
import hmac
import ipaddress
import requests
import logging
from decimal import Decimal

from utils import (
    load_ip_file,
)

from binance_data import (
    log_order_to_cache,
    safe_log_webhook_error,
)

# -------------------------
# Configuration
# -------------------------
from config._settings import (
    ALLOWED_FIELDS,
    REQUIRED_FIELDS,
    WEBHOOK_SECRET,
    SECRET_FIELD,
)

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
        logging.warning(f"[VALIDATION] Invalid JSON or Content-Type: {e}")
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
    buy_quote_pct_raw,
    buy_quote_amount_raw,
    buy_base_amount_raw,
    sell_base_pct_raw,
    sell_base_amount_raw,
    sell_quote_amount_raw,
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
        pct, amt, amount_is_base, amount_is_quote, error_response
    """

    # --- Step 1: relevant group ---
    if is_buy:
        relevant = {
            "buy_quote_pct": buy_quote_pct_raw,
            "buy_quote_amount": buy_quote_amount_raw,
            "buy_base_amount": buy_base_amount_raw,
        }
    else:
        relevant = {
            "sell_base_pct": sell_base_pct_raw,
            "sell_base_amount": sell_base_amount_raw,
            "sell_quote_amount": sell_quote_amount_raw,
        }

    # --- Step 2: ensure exactly one field is provided ---
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

    field_name, raw_value = next(iter(non_none.items()))
    logging.info(f"[FIELDS] Using {field_name}={raw_value}")

    # --- Step 3: pct field ---
    if "pct" in field_name:
        try:
            pct = Decimal(str(raw_value))
            if not (Decimal("0") < pct <= Decimal("1")):
                raise ValueError

            # pct semantics:
            # BUY → percent of quote
            # SELL → percent of base
            amount_is_quote = is_buy
            amount_is_base = not is_buy

            return pct, None, amount_is_base, amount_is_quote, None

        except Exception:
            return None, None, False, False, (
                jsonify({"error": f"{field_name} must be a number between 0 and 1."}), 400
            )

    # --- Step 4: explicit amount ---
    try:
        amt = Decimal(str(raw_value))
        if amt <= 0:
            raise ValueError
    except Exception:
        return None, None, False, False, (
            jsonify({"error": f"{field_name} must be a positive number."}), 400
        )

    # --- Step 5: flag mapping ---
    amount_is_base = field_name in ("buy_base_amount", "sell_base_amount")
    amount_is_quote = field_name in ("buy_quote_amount", "sell_quote_amount")

    return None, amt, amount_is_base, amount_is_quote, None

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

        ALLOWED_OUTBOUND_IPS = load_ip_file("config/outbound_ips.txt")

        # Convert current IP to an ipaddress object
        ip_obj = ipaddress.ip_address(current_ip)

        allowed = False
        for entry in ALLOWED_OUTBOUND_IPS:
            entry = entry.strip()
            if not entry:
                continue

            try:
                # Try to interpret entry as a network (CIDR range)
                network = ipaddress.ip_network(entry, strict=False)
                if ip_obj in network:
                    allowed = True
                    break
            except ValueError:
                # If not a valid CIDR, treat as single IP
                if current_ip == entry:
                    allowed = True
                    break

        if not allowed:
            logging.warning(f"[SECURITY] Outbound IP {current_ip} not in allowed list/ranges")
            return False, (jsonify({"error": f"Outbound IP {current_ip} not allowed"}), 403)

        return True, None

    except Exception as e:
        logging.exception(f"Failed to validate outbound IP: {e}")
        return False, (jsonify({"error": "Could not validate outbound IP"}), 500)