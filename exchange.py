import logging
from decimal import Decimal

# binance-connector imports (official SDK)
from binance.error import ClientError, ServerError

from binance_data import (
    get_cached_price,
    get_cached_balances,
    get_cached_symbol_filters,
    fetch_and_cache_balances,
    fetch_and_cache_filters,
    is_symbol_ws_excluded,
    log_order_to_cache,
    get_client,
)

from utils import (
    quantize_down,
)

from config._settings import (
    ENABLE_WS_PRICE_CACHE, 
    ENABLE_FILTER_CACHE, 
    STABLECOINS, 
    DEFAULT_QUOTE_ASSET, 
    BINANCE_RATE_LIMIT,
)

# -------------------------
# Exchange helpers (connector)
# -------------------------
def get_symbol_filters(symbol: str, rate_limit_hit: bool = False):
    """
    Return trading filters for a symbol.

    Behavior:
      - If rate_limit_hit is True → CACHE ONLY (never hit REST)
      - Else if ENABLE_FILTER_CACHE is False → always fetch via REST
      - Else → cache first, REST fallback
    """

    # ---------------------------------------------------------
    # 0) RATE LIMIT MODE → CACHE ONLY
    # ---------------------------------------------------------
    if rate_limit_hit:
        filters = get_cached_symbol_filters(symbol)
        if filters:
            logging.warning(f"[FILTER:CACHE-ONLY] Rate limit active → using cached filters for {symbol}")
            return filters

        logging.error(f"[FILTER:ABORT] Rate limit active and no cached filters for {symbol}")
        return None

    # ---------------------------------------------------------
    # 1) Global filter-cache disable → ALWAYS REST
    # ---------------------------------------------------------
    if not ENABLE_FILTER_CACHE:
        logging.info(f"[FILTER:REST-ONLY] Filter cache disabled → fetching {symbol} via REST")
        try:
            client = get_client()
            fetch_and_cache_filters(client, [symbol], log_context="REST-ONLY")

            filters = get_cached_symbol_filters(symbol)
            if filters:
                return filters

            logging.warning(f"[FILTER:REST-ONLY] REST fetch returned no filters for {symbol}")
            return None

        except Exception as e:
            logging.exception(f"[FILTER:REST-ONLY] Failed fetching filters for {symbol}: {e}")
            return None

    # ---------------------------------------------------------
    # 2) Cache enabled → Try Redis first
    # ---------------------------------------------------------
    filters = get_cached_symbol_filters(symbol)
    if filters:
        logging.info(f"[FILTER:CACHE] {symbol}: filters returned from cache")
        return filters

    logging.info(f"[FILTER:REST-FALLBACK] Cache empty → fetching {symbol} via REST")

    # ---------------------------------------------------------
    # 3) Fallback to REST
    # ---------------------------------------------------------
    try:
        client = get_client()
        fetch_and_cache_filters(client, [symbol], log_context="FALLBACK")

        filters = get_cached_symbol_filters(symbol)
        if filters:
            logging.info(f"[FILTER:REST] Successfully fetched + cached filters for {symbol}")
            return filters

        logging.warning(f"[FILTER:REST] Fallback fetch did not return filters for {symbol}")
        return None

    except Exception as e:
        logging.exception(f"[FILTER:REST] Fallback error for {symbol}: {e}")
        return None

# -------- Price (connector) --------
def get_current_price(symbol: str):
    """
    Return current price with proper rate-limit fallback to cache.
    """

    # 1) Global WS disable → always REST
    if not ENABLE_WS_PRICE_CACHE:
        logging.info(f"[PRICE:REST-ONLY] WS price cache disabled → fetching {symbol} via REST")
        price = fetch_price_via_rest(symbol)

        # RATE LIMIT → FALLBACK TO CACHE
        if price == BINANCE_RATE_LIMIT:
            cached = get_cached_price(symbol)
            if cached is not None:
                logging.warning(
                    f"[PRICE:CACHE-FALLBACK] Rate limit hit → using cached price for {symbol}: {cached}"
                )
                return cached

            logging.error(f"[PRICE:ABORT] Rate limit hit and no cached price available for {symbol}")
            return BINANCE_RATE_LIMIT

        return price

    # 2) Symbol-level exclusion → REST
    if is_symbol_ws_excluded(symbol):
        logging.info(f"[PRICE:REST-ONLY] {symbol} excluded from WS → REST")
        price = fetch_price_via_rest(symbol)

        if price == BINANCE_RATE_LIMIT:
            cached = get_cached_price(symbol)
            if cached is not None:
                logging.warning(
                    f"[PRICE:CACHE-FALLBACK] Rate limit hit → using cached price for {symbol}: {cached}"
                )
                return cached
            return BINANCE_RATE_LIMIT

        return price

    # 3) Try WebSocket cache first
    cached = get_cached_price(symbol)
    if cached is not None:
        logging.info(f"[PRICE:CACHE] {symbol}: {cached}")
        return cached

    # 4) REST fallback
    logging.info(f"[PRICE:REST-FALLBACK] {symbol} cache empty → REST")
    price = fetch_price_via_rest(symbol)

    if price == BINANCE_RATE_LIMIT:
        cached = get_cached_price(symbol)
        if cached is not None:
            logging.warning(
                f"[PRICE:CACHE-FALLBACK] Rate limit hit → using cached price for {symbol}: {cached}"
            )
            return cached
        return BINANCE_RATE_LIMIT

    return price

def fetch_price_via_rest(symbol: str):
    # detect stablecoin-vs-stablecoin pairs like USDTUSDT or USDCUSDT
    if symbol.endswith(DEFAULT_QUOTE_ASSET):
        base = symbol[: -len(DEFAULT_QUOTE_ASSET)]
        quote = DEFAULT_QUOTE_ASSET

        if base in STABLECOINS and quote in STABLECOINS:
            logging.info(f"[PRICE:SKIP] Stablecoin pair {symbol}")
            return Decimal("1")

    try:
        client = get_client()
        data = client.ticker_price(symbol)
        price = Decimal(data["price"])
        logging.debug(f"[PRICE:REST] {symbol}: {price}")
        return price

    except ClientError as e:
        msg = e.error_message.lower() if e.error_message else ""

        logging.error(f"[PRICE:REST] ClientError for {symbol}: {e.error_message}")

        if (
            e.status_code in (418, 429)
            or e.error_code in (-1003,)
            or "way too much request weight" in msg
            or "banned" in msg
        ):
            logging.warning(f"[PRICE:RATE-LIMIT] {symbol}: {e.error_message}")
            return BINANCE_RATE_LIMIT

        return None

    except Exception as e:
        logging.exception(f"[PRICE:REST] Unexpected error fetching price for {symbol}: {e}")
        return None


def get_balances():
    """
    Get account balances from Redis cache; fallback to REST.
    """
    # 1) Try cached balances first
    cached = get_cached_balances()
    if cached and len(cached) > 0:
        logging.info(f"[BALANCE:CACHE] Returning cached balances ({len(cached)} assets).")
        return cached

    # 2) Fallback: call the existing REST fetcher
    logging.warning("[BALANCE:CACHE] Cache empty or incomplete, fetching from Binance REST...")
    try:
        client = get_client()
        balances = fetch_and_cache_balances(client, log_context="FALLBACK", return_balances=True)
        if balances:
            logging.info(f"[BALANCE:REST] Successfully fetched and cached balances ({len(balances)} assets).")
            return balances
        else:
            logging.warning("[BALANCE:REST] Fallback returned no balances.")
            return {}
    except Exception as e:
        logging.exception(f"[BALANCE:REST] Fallback error while fetching balances: {e}")
        return {}

# -------------------------
# Spot functions (connector)
# -------------------------
def place_spot_market_order(symbol, side, quantity):
    """
    Place a MARKET order. Quantity must be base asset amount.
    """
    client = get_client()
    return client.new_order(symbol=symbol, side=side, type="MARKET", quantity=str(quantity))  # use str(quantity) to avoid float precision


def resolve_trade_amount(
    symbol: str,
    side: str,
    free_balance: Decimal,
    amt: Decimal | None,
    pct: Decimal | None,
    price: Decimal | None = None,
    amount_is_base: bool = False,
    amount_is_quote: bool = False,
) -> tuple[Decimal | None, str | None]:
    """
    Resolves the target trade amount based on the provided parameters.
    - amount_is_base  → amount is expressed in base-asset units (ADA)
    - amount_is_quote → amount is expressed in quote-asset units (BTC)
    """
    try:
        # ============================
        # EXPLICIT AMOUNT (amt != None)
        # ============================
        if amt is not None:

            # ---- INVALID: ambiguous flags ----
            # ---- Redundant safety: already protected in webhook layer ----
            if not amount_is_base and not amount_is_quote:
                msg = (
                    "Ambiguous amount: neither 'amount_is_base' nor 'amount_is_quote' "
                    "was set for an explicit amount. Rejecting trade."
                )
                logging.warning(f"[INVEST:{side}] {msg}")
                log_order_to_cache(symbol, side, amt, price, status="error", message=msg)
                return None, msg

            if amount_is_base and amount_is_quote:
                msg = (
                    "Invalid amount: both 'amount_is_base' and 'amount_is_quote' "
                    "cannot be true at the same time."
                )
                logging.warning(f"[INVEST:{side}] {msg}")
                log_order_to_cache(symbol, side, amt, price, status="error", message=msg)
                return None, msg

            # -------------------
            # BUY ---------------
            # -------------------
            if side == "BUY":

                if amount_is_base:
                    # Example: buy 5 ADA
                    target = amt
                    logging.info(f"[INVEST:BUY-BASE-AMOUNT] Buying {target} base units")
                    return target, None

                if amount_is_quote:
                    # Example: spend 0.01 BTC to buy ADA
                    target = amt
                    logging.info(f"[INVEST:BUY-QUOTE-AMOUNT] Spending {target} quote units")
                    return target, None

            # -------------------
            # SELL --------------
            # -------------------
            else:  # SELL

                if amount_is_base:
                    # Example: sell 0.5 ADA
                    if amt > free_balance:
                        msg = f"Balance insufficient: requested={amt}, available={free_balance}"
                        logging.warning(f"[INVEST:SELL-BASE-AMOUNT] {msg}")
                        log_order_to_cache(symbol, side, amt, price, status="error", message=msg)
                        return None, msg
                    target = amt
                    logging.info(f"[INVEST:SELL-BASE-AMOUNT] Selling {target} base units")
                    return target, None

                if amount_is_quote:
                    # Example: sell enough ADA to receive 0.01 BTC
                    if not price:
                        msg = "Missing price for quote-based sell"
                        logging.warning(f"[INVEST:SELL-QUOTE-AMOUNT] {msg}")
                        log_order_to_cache(symbol, side, "?", "?", status="error", message=msg)
                        return None, msg

                    base_equiv = amt / price
                    if base_equiv > free_balance:
                        msg = f"Balance insufficient: requested={base_equiv}, available={free_balance}"
                        logging.warning(f"[INVEST:SELL-QUOTE-AMOUNT] {msg}")
                        log_order_to_cache(symbol, side, base_equiv, price, status="error", message=msg)
                        return None, msg

                    logging.info(f"[INVEST:SELL-QUOTE-AMOUNT] Selling {base_equiv} base (≈{amt} quote)")
                    return base_equiv, None

            # Should never reach here
            msg = "Internal error: resolve_trade_amount failed to match any BUY/SELL branch"
            logging.error(msg)
            return None, msg

        # ============================
        # PERCENTAGE AMOUNT (pct != None)
        # ============================
        if pct is not None:
            resolved_amt = quantize_down(free_balance * pct, "0.00000001")
            logging.info(f"[INVEST:{side}-PERCENTAGE] Using pct={float(pct)}, resolved_amt={resolved_amt}")
            return resolved_amt, None

        # ============================
        # NO AMOUNT OR PERCENTAGE
        # ============================
        msg = "Neither amount nor percentage provided"
        logging.warning(f"[INVEST:{side}] {msg}")
        log_order_to_cache(symbol, side, "?", "?", status="error", message=msg)
        return None, msg

    except Exception as e:
        logging.warning(f"[ORDER LOG] Failed to log resolve_trade_amount error: {e}")
        return None, f"resolve_trade_amount internal error: {e}"


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

        # -------- RATE LIMIT --------
        if status in (418, 429) or code in (-1003,):
            logging.error(f"Binance rate limit hit ({status}/{code}): {e.error_message}")
            return {"error": f"Binance request limit hit ({status})"}, 429

        # -------- INSUFFICIENT BALANCE --------
        if code == -2010 or "insufficient balance" in msg:
            logging.warning(f"[ORDER] Insufficient balance for {symbol} {side}: {e.error_message}")
            return {"error": "Insufficient balance for requested trade."}, 200

        # -------- BELOW MIN NOTIONAL --------
        if "notional" in msg or code in (-1013,):
            logging.warning("Trade rejected: below Binance min_notional")
            return {"error": "Trade rejected: below Binance min_notional"}, 200

        # -------- OTHER CLIENT ERRORS --------
        logging.error(f"[ORDER] Binance ClientError {status}/{code}: {e.error_message}")
        return {"error": f"Order failed: {e.error_message}"}, 400

    logging.info(f"[ORDER] {side} successfully executed: qty={qty} {symbol} at price={price}")
    return {"status": f"spot_{side.lower()}_executed", "order": resp}, 200
