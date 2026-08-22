import json
import os
import random
import logging
import threading
import time
import redis
from decimal import Decimal
from typing import Dict, List, Optional
from urllib.parse import urlparse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from binance.websocket.spot.websocket_stream import SpotWebsocketStreamClient
from binance.spot import Spot as Client
from binance.error import ClientError
from utils import sanitize_filters
from email_poll import start_email_polling_thread
from security import is_outbound_ip_allowed

# -------------------------
# Configuration
# -------------------------
from config._settings import (
    SKIP_INITIAL_FETCH,
    GENERATE_FAKE_BALANCE_DATA,
    DELAY_API_ACCESS_SECONDS,
    BINANCE_API_KEY,
    BINANCE_SECRET_KEY,
    ALLOWED_SYMBOLS,
    WS_EXCLUDED_SUFFIXES,
    ENABLE_WS_PRICE_CACHE,
    ENABLE_FILTER_CACHE,
    DEFAULT_QUOTE_ASSET,
    STABLECOINS,
    REDIS_URL,
)

# -------------------------
# INITIALIZATION DELAY
# -------------------------
def apply_api_delay():
    """
    Delay any outward REST API access (e.g., Binance requests) to avoid
    multiple cloned servers hitting rate limits simultaneously.

    DELAY_API_ACCESS_SECONDS must be set to a valid integer.
    If the value is invalid (e.g., non-numeric), a RuntimeError is raised.

    This delay is applied both during server startup and before executing
    incoming webhook requests, ensuring staggered API usage across cloned
    deployments.
    """
    delay_raw = DELAY_API_ACCESS_SECONDS

    if delay_raw is None:
        raise RuntimeError("DELAY_API_ACCESS_SECONDS is required but missing.")

    try:
        seconds = int(delay_raw)
    except ValueError:
        raise RuntimeError(
            f"DELAY_API_ACCESS_SECONDS must be an integer, got: '{delay_raw}'"
        )

    if seconds > 0:
        logging.info(f"[DELAY] Staggering API access by {seconds} seconds...")
        time.sleep(seconds)

# -------------------------
# REDIS + WS INIT
# -------------------------
def init_all():
    """
    Initialize Binance client, Redis, WS price cache, and background
    balance/filter/snapshot caches, using config settings.
    """
    init_client(BINANCE_API_KEY, BINANCE_SECRET_KEY)
    init_redis(REDIS_URL)

    if ENABLE_WS_PRICE_CACHE:
        start_ws_price_cache(ALLOWED_SYMBOLS)
    else:
        logging.info("[WS] Price cache disabled via config flag.")

    start_background_cache(ALLOWED_SYMBOLS)
    start_email_polling_thread()
    logging.info("[INIT] Binance client, Redis, WS price cache, and background caches initialized successfully.")

# ==========================================================
# ========== CONFIG CONSTANTS ==============================
# ==========================================================
WS_LOG_INTERVAL = 47                      # Interval for logging price snapshots (seconds)
UPDATE_THROTTLE_SECONDS = 3               # 3 seconds
LAST_SEEN_UPDATE_INTERVAL = 5             # 5 seconds
BALANCE_REFRESH_INTERVAL = 3600 * 7       # 7 hour
FILTER_REFRESH_INTERVAL = 24 * 3600 * 11  # 11 day
DAILY_SNAPSHOT_INTERVAL = 24 * 3600       # 1 day
WS_RECONNECT_GRACE = 127                  # Restart stale WS streams if no update for 127s
WS_CHECK_INTERVAL = 83                    # Health monitor check interval (seconds)

DAILY_BALANCE_SNAPSHOT_KEY = "balance_snapshots"

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

def init_client(api_key: str | None = None, api_secret: str | None = None):
    """
    Initialize global Binance Spot client.

    If api_key / api_secret are not provided, fall back to config._settings.
    """
    global _client
    if _client is not None:
        return _client

    if api_key is None or api_secret is None:
        api_key = BINANCE_API_KEY
        api_secret = BINANCE_SECRET_KEY

    _client = Client(api_key=api_key, api_secret=api_secret)
    logging.info("[INIT] Binance client initialized.")
    return _client

def get_client() -> Client:
    """Return initialized Binance client, initializing lazily if needed."""
    if _client is None:
        return init_client()
    return _client

# ==========================================================
# ========== REDIS SETUP ===================================
# ==========================================================
_r = None

def get_redis() -> redis.Redis:
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

def is_symbol_ws_excluded(symbol: str) -> bool:
    """Return True if this symbol should NOT use WebSocket price caching."""
    return symbol.endswith(WS_EXCLUDED_SUFFIXES)

def filter_symbols_for_ws(symbols: list[str]) -> list[str]:
    """Return only symbols that should be tracked by WebSocket."""
    filtered = []
    excluded_count = 0

    for sym in symbols:
        if is_symbol_ws_excluded(sym):
            excluded_count += 1
        else:
            filtered.append(sym)

    logging.info(f"[WS FILTER] Excluding {excluded_count} symbols from WebSocket price cache.")
    return filtered

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
    get_redis().hset("price_cache", symbol.upper(), str(price))

def get_cached_price(symbol: str) -> Optional[Decimal]:
    """Get cached price from Redis."""
    price = get_redis().hget("price_cache", symbol.upper())
    if price is None:
        logging.info(f"[WS CACHE] No cached price yet for {symbol}")
        return None

    logging.debug(f"[WS CACHE] Returning cached price for {symbol}: {price}")
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

        get_redis().set("last_refresh_prices", now_local_ts())

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
            snapshot = get_redis().hgetall("price_cache")
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

    ws_symbols = filter_symbols_for_ws(symbols)

    threading.Thread(target=_log_price_snapshot, name="PriceLogger", daemon=True).start()
    threading.Thread(target=_ws_loop, args=(ws_symbols,), name="BinanceWSPriceCache", daemon=True).start()
    threading.Thread(target=_ws_health_monitor, args=(ws_symbols,), name="WSHealthMonitor", daemon=True).start()
    logging.info("[WS] Price cache started")

# ==========================================================
# ========== BALANCES CACHE ================================
# ==========================================================
def fetch_account_balances(client: Client) -> dict[str, Decimal]:
    """
    Fetch account balances from Binance REST and return a dict of {asset: Decimal(free)}.
    Only includes assets with a nonzero free balance.
    """
    try:
        account = client.account()
        balances = {
            b["asset"]: Decimal(str(b["free"]))
            for b in account.get("balances", [])
            if Decimal(str(b["free"])) > 0 or Decimal(str(b["locked"])) > 0
        }
        logging.debug(f"[BINANCE] Retrieved {len(balances)} balances from REST.")
        return balances
    except ClientError as e:
        logging.error(f"[BINANCE] ClientError fetching account balances: {e.error_message}")
        return {}
    except Exception as e:
        logging.exception(f"[BINANCE] Unexpected error fetching account balances: {e}")
        return {}


"""
This section periodically fetches wallet balances via Binance REST API
and caches them in Redis for quick access.
"""
def fetch_and_cache_balances(client: Client, log_context: str, return_balances: bool = False):
    """Fetch balances via REST and write them to Redis."""
    try:
        # Verify that this server's outbound IP is whitelisted by Binance
        allowed, current_ip = is_outbound_ip_allowed()
        if not allowed:
            logging.error(
                f"[CACHE:{log_context}] Outbound IP {current_ip} is not whitelisted. "
                "Skipping Binance balance fetch."
            )
            return {}

        logging.info(f"[CACHE:{log_context}] Fetching account balances from REST...")
        balances = fetch_account_balances(client)
        if not balances:
            logging.warning(f"[CACHE:{log_context}] No balances fetched; skipping cache update.")
            return {}

        ts = now_local_ts()
        data = {"balances": {k: str(v) for k, v in balances.items()}, "ts": ts}
        r = get_redis()
        r.set("account_balances", json.dumps(data))
        r.set("last_refresh_balances", ts)
        logging.info(f"[CACHE:{log_context}] Balances updated ({len(balances)} assets).")

        if return_balances:
            return balances

    except Exception as e:
        logging.exception(f"[CACHE:{log_context}] Unexpected error caching balances: {e}")
    finally:
        get_redis().set("last_refresh_balances", now_local_ts())  # Always bump timestamp, even if no data changed

def _balance_updater(client: Client):
    """Thread loop: updates balances every hour."""
    while True:
        time.sleep(BALANCE_REFRESH_INTERVAL)
        fetch_and_cache_balances(client, "PERIODIC")

def get_cached_balances() -> Optional[Dict[str, Decimal]]:
    """Return cached balances from Redis."""
    data = get_redis().get("account_balances")
    if not data:
        return None
    parsed = json.loads(data)
    return {k: Decimal(v) for k, v in parsed["balances"].items()}

def refresh_balances_for_assets(client: Client, assets: list[str]):
    """Fetch balances for specific assets and update Redis cache incrementally."""
    try:
        all_balances = fetch_account_balances(client)
        if not all_balances:
            logging.warning(f"[CACHE] Could not refresh balances — fetch failed.")
            return

        r = get_redis()
        cached = json.loads(r.get("account_balances") or '{"balances": {}, "ts": 0}')

        updated_assets = []

        for asset in assets:
            if asset in all_balances:
                cached["balances"][asset] = str(all_balances[asset])
                updated_assets.append(asset)
                logging.info(f"[CACHE] Updated {asset} balance after trade.")

        cached["ts"] = now_local_ts()
        r.set("account_balances", json.dumps(cached))

        logging.info(
            f"[CACHE] Balance refresh completed for assets: {', '.join(updated_assets) if updated_assets else 'none'}"
        )

    except Exception as e:
        logging.warning(f"[CACHE] Failed to refresh balances for {assets}: {_short_binance_error(e)}")

# ==========================================================
# ========== FILTERS CACHE =================================
# ==========================================================
"""
This section fetches trading filters (LOT_SIZE, NOTIONAL, etc.) from Binance
and caches them in Redis for efficient reuse when placing trades.
"""
def fetch_and_cache_filters(client: Client, symbols: List[str], log_context: str):
    """Fetch filters for all allowed symbols from Binance, sanitize, and cache with delta logging."""
    logging.info(f"[CACHE:{log_context}] Fetching filters for {len(symbols)} symbols...")
    r = get_redis()
    ts = now_local_ts()

    try:
        info = client.exchange_info(symbols=symbols)  # Fetch all at once
    except Exception as e:
        logging.error(f"[CACHE:{log_context}] Failed to fetch filters batch: {_short_binance_error(e)}")
        
        # If Binance says "Invalid symbol.", find which one(s)
        if "Invalid symbol" in str(e):
            logging.warning("[CACHE] Batch failed due to invalid symbol. Checking individually...")
            invalid = []

            for sym in symbols:
                try:
                    client.exchange_info(symbols=[sym])
                except Exception as e_sym:
                    if "Invalid symbol" in str(e_sym):
                        invalid.append(sym)

            logging.error(f"[CACHE] INVALID SYMBOLS FOUND: {invalid}")

        return  # stop, batch cannot continue

    updated_filters = []

    for s in info["symbols"]:
        symbol = s["symbol"].upper()
        try:
            raw_filters = {}
            for f in s["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    raw_filters["step_size"] = f.get("stepSize")
                    raw_filters["min_qty"] = f.get("minQty")
                elif f["filterType"] == "NOTIONAL":
                    raw_filters["min_notional"] = f.get("minNotional")

            filters = sanitize_filters(raw_filters)
            new_payload = {k: str(v) for k, v in filters.items()}

            # -----------------------------
            # Delta detection vs cache
            # -----------------------------
            existing_raw = r.get(f"filters:{symbol}")
            if existing_raw:
                existing = json.loads(existing_raw).get("filters", {})
            else:
                existing = None

            # Only log if something actually changed
            if existing != new_payload:
                updated_filters.append(symbol)

            # -----------------------------
            # Write to Redis
            # -----------------------------
            r.set(
                f"filters:{symbol}",
                json.dumps({"filters": new_payload, "ts": ts}),
            )

        except Exception as e:
            logging.warning(
                f"[CACHE:{log_context}] Failed to process filters for {symbol}: {_short_binance_error(e)}"
            )

    r.set("last_refresh_filters", now_local_ts())  # Always record refresh attempt

    # -----------------------------
    # Final delta summary log
    # -----------------------------
    if updated_filters:
        logging.info(
            f"[CACHE:{log_context}] Updated filters for {len(updated_filters)} symbols: {updated_filters}"
        )
    else:
        logging.info(f"[CACHE:{log_context}] Filters unchanged — no updates written.")

def _filter_updater(client: Client, symbols: List[str]):
    """Thread loop: refreshes filters."""
    while True:
        time.sleep(FILTER_REFRESH_INTERVAL)
        fetch_and_cache_filters(client, symbols, "PERIODIC")

def get_cached_symbol_filters(symbol: str) -> Optional[Dict[str, str]]:
    """Return cached filters for one symbol."""
    data = get_redis().get(f"filters:{symbol.upper()}")
    if not data:
        return None

    parsed = json.loads(data)
    filters = parsed.get("filters")

    if filters:
        logging.info(f"[FILTER:CACHE-HIT] Loaded cached filters for {symbol.upper()}")

    return filters

# ==========================================================
# ========== DAILY SNAPSHOT CACHE ==========================
# ==========================================================
def _daily_balance_snapshot_updater(client: Client):
    """Thread loop: takes a daily snapshot of total balance value."""
    first_run = True

    while True:
        if first_run:
            # Delay the very first run (avoid duplicate startup snapshot)
            time.sleep(DAILY_SNAPSHOT_INTERVAL)
            first_run = False

        try:
            take_daily_balance_snapshot(client=client)
        except Exception as e:
            logging.exception(f"[SNAPSHOT] Daily balance snapshot failed: {e}")

        time.sleep(DAILY_SNAPSHOT_INTERVAL)

def take_daily_balance_snapshot(
    balances: dict[str, Decimal] | None = None,
    client: Client | None = None
):
    """Fetch balances (if not provided) and save a daily total snapshot in Redis."""
    logging.info("[SNAPSHOT] Taking daily balance snapshot...")
    r = get_redis()

    if balances is None:
        if not client:
            raise ValueError("Either balances or client must be provided")
        logging.info("[SNAPSHOT] No balances provided, fetching from Binance...")
        balances = fetch_account_balances(client)

    # ---------------------------------------------------------
    # Fetch price for each asset and cache it
    # ---------------------------------------------------------
    from exchange import get_current_price   # avoid circular
    cached_prices = {}

    for asset, amount in balances.items():
        symbol = f"{asset}{DEFAULT_QUOTE_ASSET}"
        try:
            price = get_current_price(symbol)
            if price:
                r.hset("spot_balance_prices", symbol, str(price))
                cached_prices[symbol] = Decimal(str(price))
                logging.debug(f"[SNAPSHOT] Cached price for {symbol}: {price}")
        except Exception as e:
            logging.warning(f"[SNAPSHOT] Failed to fetch price for {symbol}: {e}")

    # ---------------------------------------------------------
    # Compute total account value
    # ---------------------------------------------------------
    total_usdt = Decimal("0")
    for asset, amount in balances.items():
        if asset in STABLECOINS:
            total_usdt += amount
        else:
            symbol = f"{asset}{DEFAULT_QUOTE_ASSET}"
            price = cached_prices.get(symbol)
            if price:
                total_usdt += amount * price

    date_str = datetime.now(TZ).strftime("%Y-%m-%d")
    snapshot = {
        "date": date_str,
        "total_usdt": str(total_usdt),
        "timestamp": now_local_ts()
    }

    if GENERATE_FAKE_BALANCE_DATA:
        generate_fake_balance_snapshots()

    r.hset(DAILY_BALANCE_SNAPSHOT_KEY, date_str, json.dumps(snapshot))
    logging.info(f"[SNAPSHOT] Stored balance snapshot for {date_str}: {total_usdt:.2f} USDT")

def generate_fake_balance_snapshots():
    """Generate 100 fake daily balance snapshots for frontend testing."""
    r = get_redis()
    today = datetime.now(TZ)

    base = 20000
    for i in reversed(range(100)):
        change = random.uniform(-5000, 70000)
        base = max(10000, base + change)

        date = today - timedelta(days=i)
        date_str = date.strftime("%Y-%m-%d")

        snapshot = {
            "date": date_str,
            "total_usdt": str(round(base, 2)),
            "timestamp": date.timestamp()
        }

        r.hset(DAILY_BALANCE_SNAPSHOT_KEY, date_str, json.dumps(snapshot))

    logging.info(f"[FAKE DATA] Inserted 100 fake balance snapshots into {DAILY_BALANCE_SNAPSHOT_KEY}")

# ==========================================================
# ========== ORDERS CACHE ==================================
# ==========================================================
def log_order_to_cache(symbol, side, qty, price, status, message):
    """Store executed or failed order info in Redis for monitoring."""
    try:
        r = get_redis()
        ts = now_local_ts()
        entry = {
            "timestamp": ts,
            "symbol": symbol,
            "side": side,
            "qty": str(qty),
            "price": str(price),
            "status": status,
            "message": message,
        }

        # Store detailed order data
        key = f"order:{ts}:{symbol}"
        r.set(key, json.dumps(entry))

        # Add reference to sorted set for easy listing (by timestamp)
        r.zadd("orders_index", {key: ts})

        # Optional: Keep the list trimmed (e.g. last 500 orders)
        r.zremrangebyrank("orders_index", 0, -501)

        logging.info(f"[CACHE] Logged order → {symbol} {side} {status}")
    except Exception as e:
        logging.warning(f"[CACHE] Failed to log order: {e}")

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

def get_cached_orders(limit: int = 100):
    """Return up to 'limit' recent orders from Redis, sorted by timestamp descending."""
    try:
        r = get_redis()
        keys = r.zrevrange("orders_index", 0, limit - 1)
        orders = []
        for k in keys:
            raw = r.get(k)
            if raw:
                orders.append(json.loads(raw))
        return orders
    except Exception as e:
        logging.error(f"[CACHE] Failed to fetch cached orders: {e}")
        return []


# ==========================================================
# ========== 12h hour period ===============================
# ==========================================================

# Fixed snapshot periods:
#
#   08:00 -> morning period
#   20:00 -> evening period
#
# The background thread does NOT determine the period.
# The current clock time does.
#
ASSET_PRICE_SNAPSHOT_CHECK_INTERVAL = 60 * 60  # every 60min

ASSET_PRICE_SNAPSHOT_PREFIX = "asset_price_snapshot"

def get_current_price_snapshot_period():
    """
    Return the fixed 12-hour snapshot period for the current time.

    Periods are:

        05:00 - 16:59 -> morning
        17:00 - 04:59 -> evening

    Returns:

        {
            "period": "2026-08-18-08",
            "period_start": datetime(...),
            "period_end": datetime(...)
        }

    The period identifier is deterministic and independent of
    application/server startup time.
    """
    now = datetime.now(TZ)

    if 5 <= now.hour < 17:
        period_start = now.replace(
            hour=5,
            minute=0,
            second=0,
            microsecond=0,
        )

    elif now.hour >= 17:
        period_start = now.replace(
            hour=17,
            minute=0,
            second=0,
            microsecond=0,
        )

    else:
        yesterday = now - timedelta(days=1)

        period_start = yesterday.replace(
            hour=17,
            minute=0,
            second=0,
            microsecond=0,
        )

    period_end = period_start + timedelta(hours=12)

    return {
        "period": period_start.strftime("%Y-%m-%d-%H"),
        "period_start": period_start,
        "period_end": period_end,
    }


def get_invested_assets() -> set[str]:
    """Return the assets currently selected as invested in the frontend."""

    r = get_redis()

    assets = r.smembers("invested_assets")

    return {
        asset.decode() if isinstance(asset, bytes) else asset
        for asset in assets
    }


def fetch_and_cache_asset_price_snapshot():
    """
    Fetch all Binance spot ticker prices in one REST request and
    cache the prices of all currently interesting assets.

    Prices are stored in the Redis hash belonging to the current
    fixed 12-hour period.

    Example:

        asset_price_snapshot:2026-08-18-08

            BTC  -> 118234.12
            ETH  -> 4521.31
            BNB  -> 823.42

    The snapshot period is determined by the clock, not by server
    startup time.
    """

    try:
        interested_assets = {
            asset.upper().strip()
            for asset in get_invested_assets()
            if asset and asset.strip()
        }

        if not interested_assets:
            logging.warning(
                "[ASSET PRICE] No interested assets found."
            )
            return None

        period_info = get_current_price_snapshot_period()

        period_id = period_info["period"]

        logging.info(
            f"[ASSET PRICE] Fetching snapshot for period "
            f"{period_id} "
            f"({period_info['period_start']} -> "
            f"{period_info['period_end']})"
        )

        client = get_client()

        # ---------------------------------------------------------
        # ONE Binance REST request
        # ---------------------------------------------------------

        all_spot_prices = client.ticker_price()

        # ---------------------------------------------------------
        # Build local lookup of USDT prices
        # ---------------------------------------------------------

        quote = DEFAULT_QUOTE_ASSET

        usdt_prices = {}

        for item in all_spot_prices:
            symbol = item.get("symbol", "")

            if not symbol.endswith(quote):
                continue

            try:
                base = symbol[:-len(quote)]
                price = Decimal(str(item["price"]))
            except (KeyError, ValueError):
                continue

            usdt_prices[base] = price

        # ---------------------------------------------------------
        # Build snapshot
        # ---------------------------------------------------------

        snapshot = {}

        for asset in interested_assets:

            # Stablecoins have a value of 1 USD.
            if asset in STABLECOINS:
                snapshot[asset] = "1"
                continue

            price = usdt_prices.get(asset)

            if price is None:
                logging.warning(
                    f"[ASSET PRICE] No {quote} price found for {asset}"
                )
                continue

            snapshot[asset] = str(price)

        if not snapshot:
            logging.warning(
                "[ASSET PRICE] No prices could be resolved."
            )
            return None

        # ---------------------------------------------------------
        # Store in Redis
        # ---------------------------------------------------------

        r = get_redis()

        redis_key = (
            f"{ASSET_PRICE_SNAPSHOT_PREFIX}:{period_id}"
        )

        r.hset(redis_key, mapping=snapshot)

        # Metadata
        r.hset(
            f"{redis_key}:meta",
            mapping={
                "period": period_id,
                "period_start": period_info["period_start"].isoformat(),
                "period_end": period_info["period_end"].isoformat(),
                "updated_at": datetime.now(TZ).isoformat(),
                "asset_count": str(len(snapshot)),
            },
        )

        # Pointer to the latest snapshot
        r.set(
            "asset_price_snapshot:last",
            redis_key,
        )

        logging.info(
            f"[ASSET PRICE] Cached {len(snapshot)} prices "
            f"under {redis_key}"
        )

        return snapshot

    except Exception as e:
        logging.exception(
            f"[ASSET PRICE] Failed to fetch price snapshot: {e}"
        )
        return None


def _asset_price_snapshot_loop():
    """
    Background loop for asset-price snapshots.

    The server does not define the snapshot periods.

    Instead, the current clock determines whether we are in the
    08:00 or 20:00 period.

    The thread wakes up periodically and only creates a new
    snapshot when the current period has not already been cached.

    This makes the system resilient to server restarts and
    deployments at arbitrary times.
    """

    logging.info(
        "[ASSET PRICE] Asset price snapshot thread started."
    )

    while True:
        try:
            period_info = get_current_price_snapshot_period()

            period_id = period_info["period"]

            r = get_redis()

            redis_key = (
                f"{ASSET_PRICE_SNAPSHOT_PREFIX}:{period_id}"
            )

            # -----------------------------------------------------
            # Only fetch once per period
            # -----------------------------------------------------

            if r.exists(redis_key):
                logging.info(
                    f"[ASSET PRICE] Snapshot already exists for "
                    f"period {period_id}; skipping fetch."
                )

            else:
                logging.info(
                    f"[ASSET PRICE] No snapshot exists for "
                    f"period {period_id}; fetching now."
                )

                fetch_and_cache_asset_price_snapshot()

        except Exception:
            logging.exception(
                "[ASSET PRICE] Unexpected error in snapshot loop."
            )

        # Check every hour whether a snapshot for the current
        # fixed 12-hour period already exists.

        logging.info(
            "[ASSET PRICE] Sleeping for an hour..."
        )
        
        time.sleep(ASSET_PRICE_SNAPSHOT_CHECK_INTERVAL)


# ==========================================================
# ========== STARTUP ENTRYPOINT =============================
# ==========================================================
"""
Called once at server startup to begin background caching threads:
- Live WebSocket price updates
- Periodic balance + filter refresh
"""
def start_background_cache(symbols: List[str]):
    """Start background threads to keep balances, filters, and prices fresh."""
    logging.info("[CACHE] Starting background threads...")
    client = get_client()

    if not SKIP_INITIAL_FETCH:
        logging.info("[CACHE] Not skipping initial REST fetch.")

        balances = fetch_and_cache_balances(client, "INIT", return_balances=True)
        if balances:
            take_daily_balance_snapshot(balances=balances)
        else:
            logging.warning("[CACHE:INIT] No balances fetched; skipping snapshot.")

        if ENABLE_FILTER_CACHE:
            fetch_and_cache_filters(client, symbols, "INIT")
    else:
        logging.info("[CACHE] Skipping initial REST fetch.")

    threading.Thread(target=_balance_updater, args=(client,), daemon=True, name="BalanceCache").start()
    threading.Thread(target=_daily_balance_snapshot_updater, args=(client,), daemon=True, name="BalanceSnapshot").start()

    if ENABLE_FILTER_CACHE:
        threading.Thread(target=_filter_updater, args=(client, symbols), daemon=True, name="FilterCache").start()
    
    # ---------------------------------------------------------
    # Periodic asset price snapshots
    # ---------------------------------------------------------
    """
    Start the background asset-price snapshot thread.

    The current fixed 12-hour period is checked immediately.
    If it does not yet have a snapshot, one is fetched.

    Subsequent checks occur every 12 hours.

    Snapshot periods are independent of server startup time.
    """
    threading.Thread(target=_asset_price_snapshot_loop, daemon=True, name="AssetPriceSnapshot").start()

    logging.info("[CACHE] Background threads started (balances, filters, and asset price snapshots)")
