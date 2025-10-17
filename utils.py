import logging
from decimal import Decimal, ROUND_DOWN, InvalidOperation
from flask import request


# ==========================================================
# ========== GENERAL UTILITIES ==============================
# ==========================================================

def should_log_request() -> bool:
    """
    Return True if the current request should be logged.
    Excludes health and root endpoints for noise reduction.
    """
    ignored_paths = ("/health-check", "/healthz", "/ping", "/")
    return request.path not in ignored_paths


def load_ip_file(path: str) -> set[str]:
    """
    Load an IP allowlist file (one IP per line).
    Returns a set of non-empty IP strings.
    """
    try:
        with open(path, "r") as f:
            return {line.strip() for line in f if line.strip()}
    except FileNotFoundError:
        logging.warning(f"[SECURITY] IP file not found: {path}")
    except Exception as e:
        logging.error(f"[SECURITY] Failed to read IP file {path}: {e}")
    return set()


def log_webhook_payload(data: dict, secret_field: str = "client_secret"):
    """
    Log incoming webhook payload while masking sensitive fields.
    """
    safe_data = {k: v for k, v in data.items() if k != secret_field}
    logging.info(f"[WEBHOOK] Received payload:\n{safe_data}")


def log_webhook_delimiter(stage: str):
    """
    Pretty visual delimiter in logs to mark webhook processing stages.
    """
    text = f" Webhook {stage} "
    border = "─" * (len(text) + 2)
    logging.info(f"┌{border}┐")
    logging.info(f"│ {text} │")
    logging.info(f"└{border}┘")


def log_parsed_payload(
    action: str,
    symbol: str,
    buy_funds_pct: str,
    buy_funds_amount: str,
    buy_crypto_amount: str,
    sell_crypto_pct: str,
    sell_crypto_amount: str,
    sell_funds_amount: str,
    trade_type: str
):
    """
    Log a parsed TradingView webhook payload summary.
    Shows only the field(s) that are actually provided (non-None).
    """
    base_msg = f"[PARSE] action={action}, symbol={symbol}, type={trade_type}"

    if action == "BUY":
        provided = {
            "buy_funds_pct": buy_funds_pct,
            "buy_funds_amount": buy_funds_amount,
            "buy_crypto_amount": buy_crypto_amount,
        }
    elif action == "SELL":
        provided = {
            "sell_crypto_pct": sell_crypto_pct,
            "sell_crypto_amount": sell_crypto_amount,
            "sell_funds_amount": sell_funds_amount,
        }
    else:
        logging.info(base_msg)
        return

    # Filter to only include fields that are not None
    non_none = {k: v for k, v in provided.items() if v is not None}

    # Append each non-None field in key=value form
    for k, v in non_none.items():
        base_msg += f", {k}={v}"

    logging.info(base_msg)


# ==========================================================
# ========== SYMBOL & FILTER UTILITIES =====================
# ==========================================================

def split_symbol(symbol: str) -> tuple[str, str]:
    """
    Split a symbol like 'BTCUSDT' into ('BTC', 'USDT').
    Supports USDT and USDC.
    """
    known_quotes = ("USDT", "USDC")
    for quote in known_quotes:
        if symbol.endswith(quote):
            return symbol[:-len(quote)], quote
    raise ValueError(f"Unknown quote asset in symbol: {symbol}")


def get_filter_value(filters: list[dict], filter_type: str, key: str):
    """
    Extract a specific value from Binance symbol filters.
    """
    for f in filters:
        if f.get("filterType") == filter_type:
            return f.get(key)
    raise ValueError(f"Filter '{filter_type}' or key '{key}' not found in filters.")


# ==========================================================
# ========== DECIMAL & QUANTIZATION HELPERS ================
# ==========================================================

def _safe_decimal(value) -> Decimal:
    """
    Convert a value to Decimal safely.
    Returns Decimal(0) on invalid input.
    """
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def quantize_down(value: Decimal, precision: str) -> Decimal:
    """
    Quantize a Decimal value to the given precision string, rounding down.

    Example:
        quantize_down(Decimal("1.23456789"), "0.0001") -> Decimal("1.2345")
    """
    try:
        step = _safe_decimal(precision)
        if step <= 0:
            logging.warning(f"[DECIMAL] Invalid precision '{precision}', returning unquantized value.")
            return value
        return value.quantize(step, rounding=ROUND_DOWN)
    except (InvalidOperation, ValueError) as e:
        logging.error(f"[DECIMAL] Quantization error: {e} (value={value}, precision={precision})")
        return value


def quantize_quantity(quantity: Decimal, step_size_str: str) -> Decimal:
    """
    Safely round down a trade quantity to conform with Binance's stepSize filter.
    If the step size is missing, None, or zero, return the original quantity.
    """
    try:
        step = _safe_decimal(step_size_str)
        if step <= 0:
            logging.warning(f"[DECIMAL] Invalid step size: '{step_size_str}', skipping quantization.")
            return quantity

        quantized = (quantity // step) * step
        return quantized.quantize(step, rounding=ROUND_DOWN)
    except (InvalidOperation, ValueError, ZeroDivisionError) as e:
        logging.error(f"[DECIMAL] Failed to quantize quantity: {e}")
        return quantity


def sanitize_filters(filters: dict) -> dict:
    """
    Ensure Binance filters (LOT_SIZE, NOTIONAL) have valid nonzero values.
    Applies safe defaults for invalid or missing data.
    """
    safe_defaults = {
        "step_size": Decimal("0.000001"),
        "min_qty": Decimal("0.00001"),
        "min_notional": Decimal("5"),
    }

    def _safe_get(key):
        try:
            val = Decimal(str(filters.get(key, "0")))
            if val <= 0:
                raise ValueError
            return val
        except (InvalidOperation, ValueError, TypeError):
            logging.warning(f"[FILTER] Using default for invalid '{key}': {safe_defaults[key]}")
            return safe_defaults[key]

    sanitized = {
        "step_size": _safe_get("step_size"),
        "min_qty": _safe_get("min_qty"),
        "min_notional": _safe_get("min_notional"),
    }

    return sanitized
