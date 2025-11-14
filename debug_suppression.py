import sys
import logging
import threading
import websocket

# ==========================================================
# ========== LOGGING NOISE SUPPRESSION =====================
# ==========================================================
"""
This section silences harmless Binance websocket disconnection tracebacks
and internal library debug spam, keeping logs clean and readable.
"""

# Disable verbose websocket-client logs
websocket.enableTrace(False)

# Silence Binance connector's internal websocket error spam
logging.getLogger("websocket").setLevel(logging.CRITICAL)
logging.getLogger("websocket._core").setLevel(logging.CRITICAL)
logging.getLogger("websocket._app").setLevel(logging.CRITICAL)
logging.getLogger("binance.websocket").setLevel(logging.CRITICAL)
logging.getLogger("binance.websockets").setLevel(logging.CRITICAL)

def _suppress_thread_exceptions(args):
    """Suppress noisy thread-level exceptions caused by websocket disconnects, Redis overload, or Binance bans."""
    msg = str(args.exc_value).lower()

    harmless_patterns = (
        "connection to remote host was lost",
        "socket is already closed",
        "websocketconnectionclosedexception",
        "connection reset by peer",
        "close frame received",
        "broken pipe",
    )

    redis_warning_patterns = (
        "max number of clients reached",
        "connection refused",
        "too many connections",
    )

    binance_rate_limit_patterns = (
        "way too much request weight used",
        "ip banned until",
        "api-key ip banned",
        "too many requests",
        "daily balance snapshot failed",
    )

    if any(p in msg for p in harmless_patterns):
        return  # Silently ignore

    if any(p in msg for p in redis_warning_patterns):
        logging.warning("Consider replacing the current Redis caching data store.")
        return

    if any(p in msg for p in binance_rate_limit_patterns):
        logging.warning("[SUPPRESSED] Binance rate-limit/IP-ban related error encountered. Skipping noisy traceback.")
        return

    # Otherwise, let real errors through
    sys.__excepthook__(args.exc_type, args.exc_value, args.exc_traceback)

threading.excepthook = _suppress_thread_exceptions
