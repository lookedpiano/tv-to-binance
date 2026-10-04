import logging
import time
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

import requests

from config._settings import (
    CMC_API_KEY,
    CMC_API_BASE_URL,
    CMC_PRICE_SNAPSHOT_PREFIX,
    CMC_PRICE_SNAPSHOT_RETENTION_DAYS,
    TZ,
)


from binance_data import get_redis


CMC_QUOTES_URL = (
    f"{CMC_API_BASE_URL}/v3/cryptocurrency/quotes/latest"
)


# ---------------------------------------------------------------------------
# CMC asset mapping
# ---------------------------------------------------------------------------
#
# IMPORTANT:
# Use CoinMarketCap IDs rather than symbols for the actual price request.
#
# CMC explicitly recommends IDs for production because symbols are not unique.
# https://coinmarketcap.com/api/documentation/guides/get-latest-crypto-prices
#
# We will populate this mapping for your Bitunix assets.
#
# Example:
#
# CMC_ASSET_IDS = {
#     "BTC": 1,
#     "ETH": 1027,
#     "SOL": 5426,
# }
#
# For now this can live in Redis instead, which is what the functions below
# support.
# ---------------------------------------------------------------------------


def _normalise_symbol(symbol):
    return str(symbol).strip().upper()


def get_cmc_headers():
    return {
        "Accept": "application/json",
        "X-CMC_PRO_API_KEY": CMC_API_KEY,
    }


# ---------------------------------------------------------------------------
# CMC ID mapping
# ---------------------------------------------------------------------------

CMC_ASSET_MAP_REDIS_KEY = (
    f"{CMC_PRICE_SNAPSHOT_PREFIX}:asset_map"
)


def set_cmc_asset_id(symbol, cmc_id):
    """
    Store one Bitunix/portfolio symbol -> CoinMarketCap ID mapping.
    """

    symbol = _normalise_symbol(symbol)

    get_redis().hset(
        CMC_ASSET_MAP_REDIS_KEY,
        symbol,
        str(int(cmc_id)),
    )


def set_cmc_asset_ids(asset_map):
    """
    Store multiple symbol -> CMC ID mappings.
    """

    if not asset_map:
        return

    mapping = {
        _normalise_symbol(symbol): str(int(cmc_id))
        for symbol, cmc_id in asset_map.items()
    }

    get_redis().hset(
        CMC_ASSET_MAP_REDIS_KEY,
        mapping=mapping,
    )


def get_cmc_asset_ids():
    """
    Return the cached CMC ID mapping.

    Example:

        {
            "BTC": 1,
            "ETH": 1027,
            "SOL": 5426,
        }
    """

    raw = get_redis().hgetall(CMC_ASSET_MAP_REDIS_KEY)

    result = {}

    for symbol, cmc_id in raw.items():

        if isinstance(symbol, bytes):
            symbol = symbol.decode()

        if isinstance(cmc_id, bytes):
            cmc_id = cmc_id.decode()

        try:
            result[symbol.upper()] = int(cmc_id)

        except (TypeError, ValueError):
            logging.warning(
                "[CMC] Invalid cached asset ID: %s -> %s",
                symbol,
                cmc_id,
            )

    return result


# ---------------------------------------------------------------------------
# Snapshot period
# ---------------------------------------------------------------------------

def get_current_cmc_price_snapshot_period():
    """
    Same 12-hour periods as the Binance implementation.

    05:00 - 16:59 -> YYYY-MM-DD-05
    17:00 - 04:59 -> YYYY-MM-DD-17
    """

    now = datetime.now(TZ)

    if 5 <= now.hour < 17:

        period_start = now.replace(
            hour=5,
            minute=0,
            second=0,
            microsecond=0,
        )

    else:

        period_start = now.replace(
            hour=17,
            minute=0,
            second=0,
            microsecond=0,
        )

        if now.hour < 5:
            period_start -= timedelta(days=1)

    period_id = period_start.strftime(
        "%Y-%m-%d-%H"
    )

    return period_id, period_start


# ---------------------------------------------------------------------------
# CMC price request
# ---------------------------------------------------------------------------

def fetch_cmc_prices(cmc_ids):
    """
    Fetch current USD prices for all supplied CMC IDs.

    IMPORTANT:
    This performs ONE HTTP request for the entire batch.

    CMC currently charges one credit per 250 returned cryptocurrencies.
    """

    if not CMC_API_KEY:
        logging.error(
            "[CMC] COINMARKETCAP_API_KEY is not configured"
        )
        return {}

    if not cmc_ids:
        return {}

    cmc_ids = sorted({
        int(cmc_id)
        for cmc_id in cmc_ids
    })

    logging.info(
        "[CMC] Fetching prices for %d assets in one request",
        len(cmc_ids),
    )

    try:

        response = requests.get(
            CMC_QUOTES_URL,
            headers=get_cmc_headers(),
            params={
                "id": ",".join(
                    str(cmc_id)
                    for cmc_id in cmc_ids
                ),
                "convert": "USD",
            },
            timeout=20,
        )

        if response.status_code == 429:

            logging.warning(
                "[CMC] Rate limit reached"
            )

            return {}

        response.raise_for_status()

        payload = response.json()

        status = payload.get("status", {})

        error_code = status.get(
            "error_code",
            0,
        )

        if error_code:

            logging.error(
                "[CMC] API error %s: %s",
                error_code,
                status.get("error_message"),
            )

            return {}

        prices = {}

        for asset in payload.get("data", []):

            try:

                cmc_id = int(
                    asset["id"]
                )

                # V3 returns quote as an ARRAY.
                usd_quote = next(
                    (
                        quote
                        for quote in asset.get(
                            "quote",
                            [],
                        )
                        if quote.get("symbol") == "USD"
                    ),
                    None,
                )

                if not usd_quote:
                    logging.warning(
                        "[CMC] No USD quote for %s",
                        asset.get("symbol"),
                    )
                    continue

                price = Decimal(
                    str(
                        usd_quote["price"]
                    )
                )

                prices[cmc_id] = price

            except (
                KeyError,
                TypeError,
                ValueError,
                InvalidOperation,
            ):

                logging.exception(
                    "[CMC] Invalid asset response: %s",
                    asset,
                )

        credit_count = (
            payload
            .get("status", {})
            .get("credit_count")
        )

        logging.info(
            "[CMC] Received %d/%d prices"
            "%s",
            len(prices),
            len(cmc_ids),
            (
                f" | credits={credit_count}"
                if credit_count is not None
                else ""
            ),
        )

        return prices

    except requests.RequestException as e:

        logging.error(
            "[CMC] Price request failed: %s",
            e,
        )

        return {}

    except Exception:

        logging.exception(
            "[CMC] Unexpected error fetching prices"
        )

        return {}


# ---------------------------------------------------------------------------
# Snapshot creation
# ---------------------------------------------------------------------------

def fetch_and_cache_cmc_asset_price_snapshot():
    """
    Create the CMC 12-hour asset price snapshot.

    Redis example:

        asset_price_snapshot_cmc:2026-10-03-05

    This is deliberately independent from Binance:

        asset_price_snapshot:...
        asset_price_snapshot_cmc:...
    """

    period_id, period_start = (
        get_current_cmc_price_snapshot_period()
    )

    redis_key = (
        f"{CMC_PRICE_SNAPSHOT_PREFIX}:{period_id}"
    )

    meta_key = (
        f"{redis_key}:meta"
    )

    last_key = (
        f"{CMC_PRICE_SNAPSHOT_PREFIX}:last"
    )

    # ---------------------------------------------------------
    # Don't overwrite an existing snapshot
    # ---------------------------------------------------------

    r = get_redis()

    if r.exists(redis_key):

        logging.info(
            "[CMC] Snapshot already exists for period %s; "
            "skipping fetch.",
            period_id,
        )

        return True

    # ---------------------------------------------------------
    # Get invested assets
    # ---------------------------------------------------------

    invested_assets = r.smembers(
        "invested_assets"
    )

    assets = set()

    for asset in invested_assets:

        if isinstance(asset, bytes):
            asset = asset.decode()

        asset = _normalise_symbol(asset)

        if asset:
            assets.add(asset)

    if not assets:

        logging.info(
            "[CMC] No invested assets found; "
            "skipping snapshot."
        )

        return False

    logging.info(
        "[CMC] Creating snapshot %s for %d invested assets",
        period_id,
        len(assets),
    )

    # ---------------------------------------------------------
    # Get CMC ID mapping
    # ---------------------------------------------------------

    cmc_asset_ids = get_cmc_asset_ids()

    requested_assets = {}
    missing_assets = []

    for symbol in assets:

        cmc_id = cmc_asset_ids.get(symbol)

        if cmc_id is None:

            missing_assets.append(symbol)

        else:

            requested_assets[symbol] = cmc_id

    if missing_assets:

        logging.warning(
            "[CMC] No CMC ID mapping for %d assets: %s",
            len(missing_assets),
            ", ".join(
                sorted(missing_assets)
            ),
        )

    if not requested_assets:

        logging.error(
            "[CMC] None of the invested assets "
            "have a CMC ID mapping."
        )

        return False

    # ---------------------------------------------------------
    # ONE CMC request
    # ---------------------------------------------------------

    cmc_prices = fetch_cmc_prices(
        requested_assets.values()
    )

    if not cmc_prices:

        logging.warning(
            "[CMC] No prices returned; "
            "snapshot was not created."
        )

        return False

    # ---------------------------------------------------------
    # Build snapshot
    # ---------------------------------------------------------

    snapshot = {}

    for symbol, cmc_id in requested_assets.items():

        price = cmc_prices.get(cmc_id)

        if price is None:

            logging.warning(
                "[CMC] No price returned for "
                "%s (CMC ID %s)",
                symbol,
                cmc_id,
            )

            continue

        snapshot[symbol] = str(price)

    if not snapshot:

        logging.warning(
            "[CMC] Snapshot contains no valid prices."
        )

        return False

    # ---------------------------------------------------------
    # Store snapshot
    # ---------------------------------------------------------

    r.hset(
        redis_key,
        mapping=snapshot,
    )

    # ---------------------------------------------------------
    # Store metadata
    # ---------------------------------------------------------

    r.hset(
        meta_key,
        mapping={
            "period_id": period_id,
            "period_start": period_start.isoformat(),
            "created_at": datetime.now(
                TZ
            ).isoformat(),
            "source": "coinmarketcap",
            "quote_currency": "USD",
            "asset_count": len(snapshot),
        },
    )

    # ---------------------------------------------------------
    # Update pointer
    # ---------------------------------------------------------

    r.set(
        last_key,
        period_id,
    )

    logging.info(
        "[CMC] Stored snapshot %s with %d/%d assets",
        period_id,
        len(snapshot),
        len(assets),
    )

    return True


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def cleanup_old_cmc_snapshots():

    _, current_period_start = (
        get_current_cmc_price_snapshot_period()
    )

    cutoff = (
        current_period_start
        - timedelta(
            days=CMC_PRICE_SNAPSHOT_RETENTION_DAYS
        )
    )

    removed = 0

    prefix = (
        f"{CMC_PRICE_SNAPSHOT_PREFIX}:"
    )

    r = get_redis()

    for key in r.scan_iter(
        match=f"{prefix}*"
    ):

        if isinstance(key, bytes):
            key = key.decode()

        # Special Redis keys.
        if key.endswith(":last"):
            continue

        if key.endswith(":meta"):
            continue

        if key.endswith(":asset_map"):
            continue

        period_id = key[len(prefix):]

        try:

            period_start = datetime.strptime(
                period_id,
                "%Y-%m-%d-%H",
            ).replace(tzinfo=TZ)

        except ValueError:
            continue

        if period_start < cutoff:

            r.delete(key)

            r.delete(
                f"{key}:meta"
            )

            removed += 1

    if removed:

        logging.info(
            "[CMC] Removed %d old snapshot Redis keys",
            removed,
        )


# ---------------------------------------------------------------------------
# Background thread
# ---------------------------------------------------------------------------

def cmc_asset_price_snapshot_loop():

    logging.info(
        "[CMC] Asset price snapshot thread started."
    )

    while True:

        try:

            period_id, _ = (
                get_current_cmc_price_snapshot_period()
            )

            redis_key = (
                f"{CMC_PRICE_SNAPSHOT_PREFIX}:"
                f"{period_id}"
            )

            if get_.exists(redis_key):

                logging.info(
                    "[CMC] Snapshot already exists "
                    "for period %s; skipping fetch.",
                    period_id,
                )

            else:

                fetch_and_cache_cmc_asset_price_snapshot()

            cleanup_old_cmc_snapshots()

        except Exception:

            logging.exception(
                "[CMC] Error in snapshot loop"
            )

        logging.info(
            "[CMC] Sleeping for 30 minutes..."
        )

        time.sleep(
            CMC_PRICE_SNAPSHOT_CHECK_INTERVAL
        )