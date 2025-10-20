import json
import sys
import os
import logging
import threading
import time
import redis
from decimal import Decimal
from typing import Dict, List, Optional
from urllib.parse import urlparse
from datetime import datetime
from zoneinfo import ZoneInfo
from binance.websocket.spot.websocket_stream import SpotWebsocketStreamClient
from binance.spot import Spot as Client
from binance.error import ClientError
from utils import sanitize_filters

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
    )

    if any(p in msg for p in harmless_patterns):
        return  # Silently ignore

    if any(p in msg for p in redis_warning_patterns):
        logging.warning("Consider replacing the current Redis caching data store.")
        return

    if any(p in msg for p in binance_rate_limit_patterns):
        logging.warning("[SUPPRESSED] Binance IP banned temporarily due to request weight. Avoid frequent redeploys.")
        return

    # Otherwise, let real errors through
    sys.__excepthook__(args.exc_type, args.exc_value, args.exc_traceback)

threading.excepthook = _suppress_thread_exceptions


# ==========================================================
# ========== CONFIG CONSTANTS ==============================
# ==========================================================
WS_LOG_INTERVAL = 42                    # Interval for logging price snapshots (seconds)
UPDATE_THROTTLE_SECONDS = 3             # 3 seconds
LAST_SEEN_UPDATE_INTERVAL = 5           # 5 seconds
BALANCE_REFRESH_INTERVAL = 3600         # 1 hour
FILTER_REFRESH_INTERVAL = 1 * 24 * 3600 # 1 day
WS_RECONNECT_GRACE = 60                 # Restart stale WS streams if no update for 60s
WS_CHECK_INTERVAL = 30                  # Health monitor check interval (seconds)


# ==========================================================
# ========== TIMEZONE CONFIG ===============================
# ==========================================================
TZ = ZoneInfo("Europe/Zurich")

def now_local_ts() -> float:
    """Return the current local timestamp (Europe/Zurich)."""
    return datetime.now(TZ).timestamp()


# ==========================================================
# ========== CLIENT ========================================
# ==========================================================
_client: Optional[Client] = None

def init_client(api_key: str, api_secret: str):
    """Initialize global Binance Spot client."""
    global _client
    if _client is None:
        _client = Client(api_key=api_key, api_secret=api_secret)
        logging.info("[INIT] Binance client initialized.")
    return _client

def get_client() -> Client:
    """Return initialized Binance client or raise."""
    if _client is None:
        raise RuntimeError("Binance client not initialized. Call init_client() first.")
    return _client


# ==========================================================
# ========== REDIS SETUP ===================================
# ==========================================================
_r = None

def _get_redis() -> redis.Redis:
    """Return the active Redis client or raise if not initialized."""
    if _r is None:
        raise RuntimeError("Redis not initialized. Call init_redis() first.")
    return _r

def init_redis(redis_url: str):
    """Initialize and log Redis connection safely."""
    global _r
    _r = redis.Redis.from_url(redis_url, decode_responses=True)

    parsed = urlparse(redis_url)
    safe_host = parsed.hostname or "unknown"
    safe_db = parsed.path.lstrip("/") or "0"

    # Mask sensitive parts for logging
    masked_host = safe_host.split("-", 1)[0] + "-******" if "-" in safe_host else safe_host
    masked_port = "******" if parsed.port else "unknown"

    logging.info(f"[REDIS] Connected (host={masked_host}:{masked_port}, db={safe_db})")


# ==========================================================
# ========== HELPER ========== =============================
# ==========================================================
def _short_binance_error(e):
    """Return a compact string for long Binance client errors."""
    text = str(e)
    if "{'Content-Type':" in text:
        text = text.split("{'Content-Type':", 1)[0] + "{...}"
    return text


# ==========================================================
# ========== PRICE CACHE (WebSocket) ========================
# ==========================================================
"""
This section maintains real-time price updates from Binance via websockets.
Each symbol gets its own dedicated WebSocket connection for maximum reliability.
If a connection goes stale (>60s without updates), it is automatically restarted.
"""
_ws_lock = threading.Lock()
_ws_started = False
_ws_clients: Dict[str, SpotWebsocketStreamClient] = {}   # active websocket clients per symbol
_last_logged: Dict[str, float] = {}                      # last logged timestamp per symbol
_last_seen = {}                                          # last time we received any message per symbol
_last_saved = {}                                         # last time we actually saved (throttled updates)

def set_cached_price(symbol: str, price: Decimal):
    """Store price in Redis hash."""
    _get_redis().hset("price_cache", symbol.upper(), str(price))

def get_cached_price(symbol: str) -> Optional[Decimal]:
    """Get cached price from Redis."""
    price = _get_redis().hget("price_cache", symbol.upper())
    if price is None:
        logging.info(f"[WS CACHE] No cached price yet for {symbol}")
        return None

    logging.info(f"[WS CACHE] Returning cached price for {symbol}: {price}")
    return Decimal(price)

def _on_ws_message(_, message):
    """Process incoming Binance websocket messages (throttled per symbol)."""
    try:
        data = json.loads(message)
        symbol = data.get("s")
        bid = data.get("b")
        ask = data.get("a")
        if not symbol or not bid or not ask:
            return

        now = time.time()

        if symbol not in _last_seen or now - _last_seen[symbol] > LAST_SEEN_UPDATE_INTERVAL:
            _last_seen[symbol] = now  # only mark as seen occasionally

        last_saved = _last_saved.get(symbol, 0)
        if now - last_saved < UPDATE_THROTTLE_SECONDS:
            return  # skip this update

        mid_price = (Decimal(str(bid)) + Decimal(str(ask))) / 2
        set_cached_price(symbol, mid_price)
        _last_saved[symbol] = now

        _get_redis().set("last_refresh_prices", now_local_ts())

        if symbol not in _last_logged or now - _last_logged[symbol] > 10:
            logging.debug(f"[WS UPDATE] {symbol}: {mid_price}")
            _last_logged[symbol] = now

    except Exception as e:
        logging.exception(f"[WS] Message processing failed: {e}")

def _start_ws_for_symbol(symbol: str):
    """Start a dedicated WebSocket client for a single trading pair."""
    stream = f"{symbol.lower()}@bookTicker"
    while True:
        try:
            client = SpotWebsocketStreamClient(on_message=_on_ws_message)
            client.subscribe(stream=stream)
            _ws_clients[symbol] = client
            logging.debug(f"[WS] Started dedicated WebSocket for {symbol}")
            return
        except Exception as e:
            logging.error(f"[WS] Failed to start WebSocket for {symbol}: {e}. Retrying in 5s...")
            time.sleep(5)

def _ws_loop(symbols: List[str]):
    """
    WebSocket loop to keep Redis price cache updated.
    Starts one dedicated WebSocket per symbol for robustness.
    """
    logging.debug(f"[DEBUG] WS loop PID={os.getpid()}, ID={threading.get_ident()}")

    for sym in symbols:
        threading.Thread(target=_start_ws_for_symbol, args=(sym,), daemon=True, name=f"WS-{sym}").start()
        time.sleep(1)  # gentle stagger to avoid hitting API rate limits

    logging.info("[WS] All dedicated WebSocket clients started successfully.")

def _ws_health_monitor(symbols: List[str]):
    """Monitor each WebSocket stream and restart if stale (>WS_RECONNECT_GRACE seconds without updates)."""
    while True:
        time.sleep(WS_CHECK_INTERVAL)
        now = time.time()
        for sym in symbols:
            last_seen = _last_seen.get(sym, 0)
            if now - last_seen > WS_RECONNECT_GRACE:
                logging.info(f"[WS MONITOR] {sym} stale for >{WS_RECONNECT_GRACE}s — restarting...")
                client = _ws_clients.pop(sym, None)
                if client:
                    try:
                        client.stop()
                    except Exception:
                        pass
                threading.Thread(target=_start_ws_for_symbol, args=(sym,), daemon=True).start()
                _last_seen[sym] = now

def _log_price_snapshot():
    """Periodically log snapshot of all cached prices."""
    while True:
        time.sleep(WS_LOG_INTERVAL)
        try:
            snapshot = _get_redis().hgetall("price_cache")
            if not snapshot:
                logging.info("[WS SNAPSHOT] Cache empty (not yet populated).")
                continue
            joined = ", ".join(f"{k}={v}" for k, v in snapshot.items())
            logging.debug(f"[WS SNAPSHOT] {joined}")
        except Exception as e:
            logging.error(f"[WS SNAPSHOT] Failed to read Redis cache: {e}")

def start_ws_price_cache(symbols: List[str]):
    """Start background websocket threads for price updates."""
    global _ws_started
    with _ws_lock:
        if _ws_started:
            logging.info("[WS] Already running")
            return
        _ws_started = True

    threading.Thread(target=_log_price_snapshot, name="PriceLogger", daemon=True).start()
    threading.Thread(target=_ws_loop, args=(symbols,), name="BinanceWSPriceCache", daemon=True).start()
    threading.Thread(target=_ws_health_monitor, args=(symbols,), name="WSHealthMonitor", daemon=True).start()
    logging.info("[WS] Price cache started")


# ==========================================================
# ========== BALANCES CACHE ================================
# ==========================================================
"""
This section periodically fetches wallet balances via Binance REST API
and caches them in Redis for quick access.
"""
def fetch_and_cache_balances(client: Client):
    """Fetch balances via REST and write them to Redis."""
    try:
        logging.info("[CACHE] Fetching account balances from REST...")
        account = client.account()
        balances = {
            b["asset"]: Decimal(str(b["free"]))
            for b in account["balances"]
            if Decimal(str(b["free"])) > 0
        }
        ts = now_local_ts()
        data = {"balances": {k: str(v) for k, v in balances.items()}, "ts": ts}
        r = _get_redis()
        r.set("account_balances", json.dumps(data))
        r.set("last_refresh_balances", ts)
        logging.info(f"[CACHE] Balances updated ({len(balances)} assets).")
    except ClientError as e:
        logging.error(f"[CACHE] Binance error fetching balances: {e.error_message}")
    except Exception as e:
        logging.exception(f"[CACHE] Unexpected error fetching balances: {e}")
    finally:
        _get_redis().set("last_refresh_balances", now_local_ts())  # Always bump timestamp, even if no data changed

def _balance_updater(client: Client):
    """Thread loop: updates balances every hour."""
    while True:
        fetch_and_cache_balances(client)
        time.sleep(BALANCE_REFRESH_INTERVAL)

def get_cached_balances() -> Optional[Dict[str, Decimal]]:
    """Return cached balances from Redis."""
    data = _get_redis().get("account_balances")
    if not data:
        return None
    parsed = json.loads(data)
    return {k: Decimal(v) for k, v in parsed["balances"].items()}

def refresh_balances_for_assets(client: Client, assets: List[str]):
    """Fetch balances for specific assets and update Redis cache incrementally."""
    try:
        account = client.account()
        all_balances = {b["asset"]: Decimal(str(b["free"])) for b in account["balances"]}
        r = _get_redis()
        cached = json.loads(r.get("account_balances") or '{"balances": {}, "ts": 0}')
        for asset in assets:
            if asset in all_balances:
                cached["balances"][asset] = str(all_balances[asset])
                logging.info(f"[CACHE] Updated {asset} balance after trade.")
        cached["ts"] = now_local_ts()
        r.set("account_balances", json.dumps(cached))
    except Exception as e:
        logging.warning(f"[CACHE] Failed to refresh balances for {assets}: {_short_binance_error(e)}")


# ==========================================================
# ========== FILTERS CACHE =================================
# ==========================================================
"""
This section fetches trading filters (LOT_SIZE, NOTIONAL, etc.) from Binance
and caches them in Redis for efficient reuse when placing trades.
"""
def fetch_and_cache_filters(client: Client, symbols: List[str]):
    """Fetch filters for all allowed symbols from Binance, sanitize, and cache."""
    logging.info(f"[CACHE] Fetching filters for {len(symbols)} symbols...")
    r = _get_redis()
    ts = now_local_ts()

    for symbol in symbols:
        try:
            info = client.exchange_info(symbol=symbol)
            s = info["symbols"][0]

            raw_filters = {}
            for f in s["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    raw_filters["step_size"] = f.get("stepSize")
                    raw_filters["min_qty"] = f.get("minQty")
                elif f["filterType"] == "NOTIONAL":
                    raw_filters["min_notional"] = f.get("minNotional")

            filters = sanitize_filters(raw_filters)
            r.set(
                f"filters:{symbol.upper()}",
                json.dumps({"filters": {k: str(v) for k, v in filters.items()}, "ts": ts}),
            )
            logging.debug(f"[CACHE] Filters cached for {symbol}")

        except Exception as e:
            logging.warning(f"[CACHE] Failed to cache filters for {symbol}: {_short_binance_error(e)}")

    r.set("last_refresh_filters", now_local_ts())  # Always record that a refresh attempt happened

def _filter_updater(client: Client, symbols: List[str]):
    """Thread loop: refreshes filters daily."""
    while True:
        fetch_and_cache_filters(client, symbols)
        time.sleep(FILTER_REFRESH_INTERVAL)

def get_cached_symbol_filters(symbol: str) -> Optional[Dict[str, str]]:
    """Return cached filters for one symbol."""
    data = _get_redis().get(f"filters:{symbol.upper()}")
    if not data:
        return None
    parsed = json.loads(data)
    return parsed.get("filters")


# ==========================================================
# ========== STARTUP ENTRYPOINT =============================
# ==========================================================
"""
Called once at server startup to begin background caching threads:
- Live WebSocket price updates
- Periodic balance + filter refresh
"""
def start_background_cache(symbols: List[str]):
    """Start background threads to keep balances and filters fresh."""
    logging.info("[CACHE] Starting background threads...")
    client = get_client()
    fetch_and_cache_balances(client)
    fetch_and_cache_filters(client, symbols)
    threading.Thread(target=_balance_updater, args=(client,), daemon=True, name="BalanceCache").start()
    threading.Thread(target=_filter_updater, args=(client, symbols), daemon=True, name="FilterCache").start()
    logging.info("[CACHE] Background threads started")
