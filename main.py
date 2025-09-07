from flask import Flask, request, jsonify
import hmac
import requests
import time
import os
import logging
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from datetime import datetime, timezone
from requests.exceptions import HTTPError
from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceRequestException



# -------------------------
# Logging configuration
# -------------------------
logging.basicConfig(
    level=logging.INFO,
    format='[%(levelname)s] %(message)s'
)

app = Flask(__name__)


# -------------------------
# Environment variables
# -------------------------
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY")
BINANCE_SECRET_KEY = os.environ.get("BINANCE_SECRET_KEY")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET")
PORT = os.environ.get("PORT")

if not BINANCE_API_KEY:
    raise RuntimeError("Missing required environment variable: BINANCE_API_KEY")
if not BINANCE_SECRET_KEY:
    raise RuntimeError("Missing required environment variable: BINANCE_SECRET_KEY")
if not WEBHOOK_SECRET:
    raise RuntimeError("Missing required environment variable: WEBHOOK_SECRET")
if not PORT:
    raise RuntimeError(
        "Missing required environment variable: PORT.\n"
        "The following ports are reserved by Render and cannot be used: 18012, 18013 and 19099.\n"
        "Choose a port such that: 1024 < PORT <= 49000, excluding the reserved ones."
    )


# -----------------------------
# CLIENT INIT
# -----------------------------
client = Client(BINANCE_API_KEY, BINANCE_SECRET_KEY)


# -------------------------
# Configuration
# -------------------------
ALLOWED_SYMBOLS = {"BTCUSDT", "ETHUSDT", "ADAUSDT", "DOGEUSDT", "PEPEUSDT", "XRPUSDT", "WIFUSDT", "BNBUSDT"}
SECRET_FIELD = "client_secret"
WEBHOOK_REQUEST_PATH = "/to-the-moon"
MAX_CROSS_LEVERAGE = 3
# Allowlist of known TradingView alert IPs (must keep updated)
# See: https://www.tradingview.com/support/solutions/43000529348
TRADINGVIEW_IPS = {
    "52.89.214.238",
    "34.212.75.30",
    "54.218.53.128",
    "52.32.178.7"
}


# -------------------------
# Utilities
# -------------------------
def should_log_request():
    return request.path not in ('/health-check', '/healthz', '/ping', '/')

def quantize_quantity(quantity: Decimal, step_size_str: str) -> Decimal:
    """Round down quantity to conform to stepSize."""
    step = Decimal(step_size_str)
    # floor to step multiple
    quant = (Decimal(quantity) // step) * step
    # quantize to the same scale as step
    return quant.quantize(step, rounding=ROUND_DOWN)

def get_filter_value(filters, filter_type, key):
    for f in filters:
        if f.get("filterType") == filter_type:
            return f.get(key)
    raise ValueError(f"{filter_type} or key '{key}' not found in filters.")

def log_webhook_delimiter(at_point: str):
    line = f" Webhook {at_point} "
    border = "─" * (len(line) + 2)
    logging.info(f"┌{border}┐")
    logging.info(f"│ {line} │")
    logging.info(f"└{border}┘")

def log_parsed_payload(action, symbol, buy_pct_raw, amt_raw, trade_type, leverage_raw=None):
    """
    Logs the parsed payload fields. Includes leverage only if type is MARGIN.
    """
    log_msg = (
        f"[PARSE] action={action}, symbol={symbol}, "
        f"buy_pct={buy_pct_raw}, amount={amt_raw}, type={trade_type}"
    )
    if trade_type == "MARGIN":
        log_msg += f", leverage={leverage_raw}"
    
    logging.info(log_msg)


# -----------------------
# Validation functions
# -----------------------
def validate_json():
    """Validate that the incoming request contains valid JSON."""
    try:
        data = request.get_json(force=False, silent=False)
        if not isinstance(data, dict):
            raise ValueError("Payload is not a valid JSON object.")
        return data, None
    except Exception as e:
        raw = request.data.decode("utf-8", errors="ignore")
        logging.exception(f"[FATAL ERROR] Failed to parse JSON payload: {e}")
        logging.info(f"[RAW DATA]\n{raw}")
        return None, jsonify({"error": "Invalid JSON payload"}), 400

def validate_secret(data):
    """Validate that the webhook secret is correct."""
    secret_from_request = data.get(SECRET_FIELD)
    if not secret_from_request or not hmac.compare_digest(str(secret_from_request), str(WEBHOOK_SECRET)):
        logging.warning("[SECURITY] Unauthorized attempt (invalid or missing secret)")
        return False, jsonify({"error": "Unauthorized"}), 401
    return True, None

def validate_order_qty(symbol: str, qty: Decimal, price: Decimal, min_qty: Decimal, min_notional: Decimal) -> tuple[bool, dict, int]:
    """
    Validate order quantity and notional against exchange filters.
    Returns (is_valid, response_dict, http_status).
    If invalid, response_dict contains a warning.
    """

    total_investment = qty * price
    logging.info(f"[INVESTMENT] Approx. total investment ≈ {total_investment:.2f} USDT --> price={price}, qty={qty}")

    logging.info(f"[SAFEGUARDS] Validate order qty for {symbol}: {qty}")
    if qty <= Decimal("0"):
        logging.warning("Trade qty is zero or negative after rounding. Aborting.")
        return False, {"warning": "Calculated trade size too small after rounding"}, 200

    if qty < min_qty:
        logging.warning(f"Trade qty {qty} is below min_qty {min_qty}. Aborting.")
        return False, {"warning": f"Trade qty {qty} is below min_qty {min_qty}"}, 200

    if (qty * price) < min_notional:
        logging.warning(f"Trade notional {qty*price} is below min_notional {min_notional}. Aborting.")
        return False, {"warning": f"Trade notional {qty*price} is below min_notional {min_notional}"}, 200

    logging.info("[SAFEGUARDS] Successfully validated. Proceeding with trade order placement.")
    return True, {}, 200

def validate_and_normalize_buy_fields(is_buy: bool, buy_pct_raw, amt_raw):
    """
    Validates that exactly one of buy_pct_raw or amt_raw is provided for buy orders,
    converts them to Decimal, ensures numeric & positive, and returns:
        (buy_pct: Decimal | None, amount: Decimal | None, error_response: Response | None)
    For sell orders, both are ignored and return (None, None, None)
    """
    # Skip validation if not a BUY order
    if not is_buy:
        return None, None, None

    # Case 1: both provided → reject
    if buy_pct_raw is not None and amt_raw is not None:
        logging.error("Both buy_pct and amount provided — only one is allowed.")
        return None, None, (jsonify({"error": "Please provide either buy_pct or amount, not both."}), 400)

    # Case 2: neither provided → reject
    if buy_pct_raw is None and amt_raw is None:
        logging.error("Neither buy_pct nor amount provided — one is required for a buy order.")
        return None, None, (jsonify({"error": "Please provide either buy_pct or amount."}), 400)
    
    # If buy_pct was provided → check numeric & range
    if buy_pct_raw is not None:
        try:
            buy_pct = Decimal(str(buy_pct_raw))
            if not (Decimal("0") < buy_pct <= Decimal("1")):
                logging.error(f"buy_pct out of range: {buy_pct_raw}")
                return None, None, (jsonify({"error": "buy_pct must be a number between 0 and 1."}), 400)
        except (InvalidOperation, ValueError):
            logging.error(f"Invalid buy_pct value: {buy_pct_raw}")
            return None, None, (jsonify({"error": "buy_pct must be a valid number between 0 and 1."}), 400)
        return buy_pct, None, None

    # If amount was provided → check numeric & positive
    if amt_raw is not None:
        try:
            amt = Decimal(str(amt_raw))
            if amt <= 0:
                logging.error(f"amount must be positive, got: {amt_raw}")
                return None, None, (jsonify({"error": "amount must be greater than zero."}), 400)
        except (InvalidOperation, ValueError):
            logging.error(f"Invalid amount value: {amt_raw}")
            return None, None, (jsonify({"error": "amount must be a valid number."}), 400)
        return None, amt, None


# -------------------------
# Exchange helpers
# -------------------------
def get_symbol_filters(symbol: str):
    """
    Fetch symbol filters (lot size, min notional, etc.) for a given symbol.
    """
    try:
        info = client.get_symbol_info(symbol)
        if not info:
            return []
        filters = info.get("filters", [])
        # log_filters(symbol, filters)
        return filters
    except BinanceAPIException as e:
        logging.error(f"Binance API error while fetching filters for {symbol}: {e.message}")
        return []
    except Exception as e:
        logging.exception(f"Failed to fetch exchangeInfo for {symbol}: {e}")
        return []

def get_min_notional(filters):
    # Attempt to retrieve the MIN_NOTIONAL filter
    min_notional = next((f['minNotional'] for f in filters if f['filterType'] == 'MIN_NOTIONAL'), None)
    if min_notional:
        return Decimal(min_notional)
    
    # If MIN_NOTIONAL is not found, check for the NOTIONAL filter
    notional_filter = next((f for f in filters if f['filterType'] == 'NOTIONAL'), None)
    if notional_filter:
        return Decimal(notional_filter['minNotional'])
    
    # If neither filter is found, return a default value
    return Decimal('0.0')

def get_trade_filters(symbol):
    """Fetch filters and return step_size, min_qty, min_notional as Decimals or (None, None, None) on failure."""
    filters = get_symbol_filters(symbol)
    if not filters:  # no filters available
        logging.warning(f"No filters found for {symbol}")
        return None, None, None

    try:
        step_size_val = get_filter_value(filters, "LOT_SIZE", "stepSize")
        min_qty_val = get_filter_value(filters, "LOT_SIZE", "minQty")
        
        min_notional = get_min_notional(filters)

        step_size = Decimal(step_size_val)
        min_qty = Decimal(min_qty_val)

        logging.info(f"[FILTERS] step_size={step_size}, min_notional={min_notional}, min_qty={min_qty}")
        return step_size, min_qty, min_notional

    except Exception as e:
        logging.exception(f"Error parsing filters for {symbol}: {e}")
        return None, None, None

def get_current_price(symbol: str):
    """
    Fetch current price for a symbol using Binance client.
    """
    try:
        data = client.get_symbol_ticker(symbol=symbol)
        price = Decimal(str(data["price"]))
        logging.info(f"[PRICE] {symbol}: {price}")
        return price
    
    except BinanceAPIException as e:
        logging.error(f"Binance API error while fetching price for {symbol}: {e.message}")
        if e.status_code == 418:
            logging.warning(f"Rate limit hit or temp block for {symbol}:<{e}>")
            #logging.warning(f"Rate limit hit or temp block for {symbol}. Retrying in 5s...")
            #time.sleep(5)
            # retry once
            #return get_current_price(symbol)
            return None
        if e.status_code == 429:
            logging.warning(f"Request limit hit or temp block for {symbol}:<{e}>")
            return None
        else:
            logging.exception(f"HTTP error for {symbol}:<{e}>")
            return None  # or raise again if you want it to bubble up
    
    except BinanceRequestException as e:
        # Network or connection issues
        logging.error(f"Binance request error for {symbol}: {e}")
        return None
    
    except Exception as e:
        logging.exception(f"Unexpected error fetching price for {symbol}: {e}")
        return None

def snapshot_balances():
    """Take snapshots of spot and margin balances > 0"""
    spot_balances = {
        b['asset']: Decimal(b['free']) + Decimal(b['locked'])
        for b in client.get_account()['balances']
        if Decimal(b['free']) + Decimal(b['locked']) > 0
    }

    '''
    margin_balances = {
        b['asset']: Decimal(b['free']) + Decimal(b['locked'])
        for b in client.get_margin_account()['userAssets']
        if Decimal(b['free']) + Decimal(b['locked']) > 0
    }

    return {"spot": spot_balances, "margin": margin_balances}
    '''
    return {"spot": spot_balances}

def compare_spot_balances(spot_before, spot_after):
    """Compare spot balances before vs. after and log/print differences"""
    if spot_before == spot_after:
        logging.info("[SANITY CHECK] Spot balances unchanged after margin trade.")
    else:
        logging.warning("[SANITY CHECK] Spot balances changed!")

        # Find and report differences
        all_assets = set(spot_before.keys()) | set(spot_after.keys())
        for asset in all_assets:
            before_amt = spot_before.get(asset, Decimal("0"))
            after_amt = spot_after.get(asset, Decimal("0"))
            if before_amt != after_amt:
                diff = after_amt - before_amt
                logging.warning(f"{asset}: {before_amt} → {after_amt} (diff: {diff})")


# -------------------------
# Spot functions
# -------------------------
def get_spot_asset_free(asset: str) -> Decimal:
    """
    Return free balance for asset from spot account as Decimal.
    """
    try:
        account_info = client.get_account()
        balances = account_info.get("balances", [])
        # log_balances(balances)
        for b in balances:
            if b.get("asset") == asset:
                free = Decimal(str(b.get("free", "0")))
                logging.info(f"[SPOT BALANCE] {asset} free={free}")
                return free
        return Decimal("0")
    except BinanceAPIException as e:
        logging.error(f"Binance API error while fetching {asset} balance: {e.message}")
        raise
    except Exception as e:
        logging.exception(f"Failed to fetch spot asset balance for {asset}: {e}")
        raise

def place_spot_market_order(symbol, side, quantity):
    return client.order_market(symbol=symbol, side=side, quantity=float(quantity))

#TODO: prepared - not in use yet
def place_margin_market_order(symbol, side, quantity):
    return client.create_margin_order(
                        symbol=symbol,
                        side=side,
                        type="MARKET",
                        quantity=float(quantity),
                        sideEffectType="MARGIN_BUY" if side == "BUY" else "NO_SIDE_EFFECT",
                        isIsolated=False
                    )

def resolve_invest_usdt(usdt_free, amt, buy_pct) -> tuple[Decimal | None, str | None]:
    """
    Decide how much USDT to invest.
    
    Returns:
        (invest_usdt, error_message)
        - invest_usdt (Decimal) if valid, else None
        - error_message (str) if invalid, else None
    """
    if amt is not None:
        if amt > usdt_free:
            logging.warning(f"[INVEST:AMOUNT] Balance insufficient: requested amount={amt}, available={usdt_free}")
            return None, f"Balance insufficient: requested={amt}, available={usdt_free}"

        logging.info(f"[INVEST:AMOUNT] Using explicit amount={amt}")
        return amt, None
    
    # Use buy_pct if amt_raw is missing
    invest_usdt = (usdt_free * buy_pct).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
    logging.info(f"[INVEST:BUY-PERCENTAGE] Using buy_pct={buy_pct}, invest_usdt={invest_usdt}")
    return invest_usdt, None

def place_order_with_handling(symbol: str, side: str, qty: Decimal, price: Decimal, place_order_fn):
    """
    Place an order safely with unified exception handling and logging.
    Returns (response_dict, status_code).
    """
    try:
        resp = place_order_fn(symbol, side, qty)
    except requests.exceptions.HTTPError as e:
        err_str = str(e).lower()
        if "418" in err_str or "teapot" in err_str:
            logging.error(f"Binance rate limit hit (418): {e}")
            return {"error": "Binance rate limit hit (418 I'm a teapot)"}, 429
        elif "429" in err_str or "too many requests" in err_str:
            logging.error(f"Binance request limit hit (429): {e}")
            return {"error": "Binance request limit hit (429)"}, 429
        elif "notional" in err_str:
            logging.error("Trade rejected: below Binance min_notional")
            return {"error": "Trade rejected: below Binance min_notional"}, 400
        else:
            raise

    logging.info(f"[ORDER] {side} successfully executed: {qty} {symbol} at {price} on {datetime.now(timezone.utc).isoformat()}")
    return {"status": f"spot_{side.lower()}_executed", "order": resp}, 200


# -------------------------
# Margin functions
# -------------------------
def _normalize_leverage(leverage_raw) -> tuple[int | None, str | None]:
    try:
        if isinstance(leverage_raw, bool):
            raise ValueError

        L = int(leverage_raw)

        if str(leverage_raw) != str(L):
            raise ValueError
        
        if 1 <= L <= MAX_CROSS_LEVERAGE:
            return L, None
        else:
            raise ValueError

    except Exception:
        return None, f"Leverage not valid. leverage must be an integer from 1 to {MAX_CROSS_LEVERAGE}"

def _get_margin_free(asset: str) -> Decimal:
    acc = client.get_margin_account()
    for a in acc.get("userAssets", []):
        if a.get("asset") == asset:
            free = Decimal(str(a.get("free", "0")))
            logging.info(f"[MARGIN BALANCE] {asset} free={free}")
            return free
    return Decimal("0")

def _get_margin_debt(asset: str) -> Decimal:
    acc = client.get_margin_account()
    for a in acc.get("userAssets", []):
        if a.get("asset") == asset:
            borrowed = Decimal(str(a.get("borrowed", "0")))
            interest = Decimal(str(a.get("interest", "0")))
            total_debt = borrowed + interest
            logging.info(f"[MARGIN DEBT] {asset}: borrowed={borrowed}, interest={interest} => total debt: {total_debt}")
            return total_debt
    return Decimal("0")


# ---------------------------------
# Unified trade execution
# ---------------------------------
def execute_trade(symbol: str, side: str, buy_pct=None, amt=None, trade_type: str ="SPOT", leverage_raw=None, place_order_fn=None):
    """
    Unified trade executor for SPOT and Cross-Margin.
    - Handles buy/sell, quantity math, filter validation, and order placement.
    - No auto-transfer from Spot to Margin (you handle transfers manually).
    - Margin BUY uses sideEffectType="MARGIN_BUY" (auto-borrow).
    - Margin SELL uses sideEffectType="AUTO_REPAY" (auto-repay of the asset being sold)
    and we additionally repay any USDT debt using the sale proceeds.
    Returns (response_dict, http_status).
    """
    try:
        # Fetch price and filters
        price = get_current_price(symbol)
        if price is None:
            logging.info(f"Retrying once for {symbol}. Retrying in 3 seconds...")
            time.sleep(3)
            price = get_current_price(symbol)
        if price is None:
            logging.warning(f"No price available for {symbol}. Cannot proceed.")
            return {"error": f"Price not available for {symbol}"}, 200

        step_size, min_qty, min_notional = get_trade_filters(symbol)
        if None in (step_size, min_qty, min_notional):
            logging.warning(f"Incomplete trade filters for {symbol}: step_size={step_size}, min_qty={min_qty}, min_notional={min_notional}")
            return {"error": f"Filters not available for {symbol}"}, 200
        
        # bottleneck for margin trades
        if trade_type == "MARGIN":
            # TODO: find a way to secure two signals (spot and margin)
            # logging.info(f"Waiting for possible spot buy to be over. Proceeding in 7 seconds...")
            # time.sleep(7)
            logging.warning("MARGIN-trading to be implemented. We'll be right back...")
            return {"error": "MARGIN-trading not yet implemented."}, 200

        # BUY flow
        if side == "BUY":
            if trade_type == "SPOT":
                # SPOT buy -> use spot USDT balance
                try:
                    usdt_free = get_spot_asset_free("USDT")
                    invest_usdt, error_msg = resolve_invest_usdt(usdt_free, amt, buy_pct)
                    if error_msg:
                        logging.warning(f"[INVEST ERROR] {error_msg}")
                        return {"error": error_msg}, 200
                    raw_qty = invest_usdt / price
                    qty = quantize_quantity(raw_qty, step_size)
                    logging.info(f"[EXECUTE SPOT BUY] {symbol}: invest={invest_usdt}, qty={qty}, raw_qty={raw_qty:.16f}")
                    is_valid, resp_dict, status = validate_order_qty(symbol, qty, price, min_qty, min_notional)
                    if not is_valid:
                        return resp_dict, status
                    
                    # Place the order after safeguards pass
                    return place_order_with_handling(symbol, side, qty, price, place_order_fn)
                                
                except Exception as e:
                    logging.exception("Spot buy failed")
                    return {"error": f"Spot buy failed: {str(e)}"}, 500
            elif trade_type == "MARGIN":
                # MARGIN buy -> operate only on margin account (no spot fallback) : TODO: check if thats so
                # Cross-Margin BUY (long with optional borrowing)
                try:
                    before = snapshot_balances()

                    usdt_free = _get_margin_free("USDT")
                    invest_usdt, error_msg = resolve_invest_usdt(usdt_free, amt, buy_pct)
                    if error_msg:
                        logging.warning(f"[MARGIN INVEST ERROR] {error_msg}")
                        return {"error": error_msg}, 200

                    leverage, error_msg = _normalize_leverage(leverage_raw)
                    if error_msg:
                        logging.warning(f"[MARGIN LEVERAGE ERROR] {error_msg}")
                        return {"error": error_msg}, 200

                    max_borrow = (usdt_free * (leverage - Decimal("1"))).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
                    needed_borrow = invest_usdt - usdt_free if invest_usdt > usdt_free else Decimal("0")
                    logging.info(f"[MARGIN BUY] max_borrow={max_borrow}, needed_borrow={needed_borrow}")
                    if needed_borrow > max_borrow:
                        logging.warning(f"Requested borrow {needed_borrow} exceeds leverage cap {max_borrow} (leverage={leverage}). Clamping invest amount.")
                        invest_usdt = usdt_free + max_borrow

                    raw_qty = invest_usdt / price
                    qty = quantize_quantity(raw_qty, step_size)
                    logging.info(f"[EXECUTE MARGIN BUY] {symbol}: invest={invest_usdt}, leverage={leverage}, qty={qty}, raw_qty={raw_qty:.16f}")
                    is_valid, resp_dict, status = validate_order_qty(symbol, qty, price, min_qty, min_notional)
                    if not is_valid:
                        return resp_dict, status

                    # Place market margin order with auto-borrow
                    resp = client.create_margin_order(
                        symbol=symbol,
                        side="BUY",
                        type="MARKET",
                        quantity=float(qty),
                        sideEffectType="MARGIN_BUY",
                        isIsolated=False
                    )
                    logging.info(f"[MARGIN ORDER] {side} successfully executed: {qty} {symbol} at {price} on {datetime.now(timezone.utc).isoformat()}")
                    
                    after = snapshot_balances()
                    # Compare spot balances only
                    compare_spot_balances(before["spot"], after["spot"])

                    return {"status": "margin_buy_executed", "order": resp, "leverage_used": str(leverage)}, 200

                except BinanceAPIException as e:
                    logging.error(f"Binance API error during margin buy: {e.message}")
                    return {"error": f"Margin buy failed: {e.message}"}, 500
                except Exception as e:
                    logging.exception("Margin buy failed")
                    return {"error": f"Margin buy failed: {str(e)}"}, 500
            else:
                return {"error": f"Unknown trade type {trade_type}"}, 400

        # SELL flow
        elif side == "SELL":
            # We'll use base asset name
            base_asset = symbol.replace("USDT", "")
            if trade_type == "SPOT":
                # Sell on spot account only
                try:
                    asset_free = get_spot_asset_free(base_asset)
                    if asset_free <= Decimal("0"):
                        logging.warning(f"No spot {base_asset} balance to sell. Aborting.")
                        response = {"warning": f"No spot {base_asset} balance to sell. Aborting."}, 200
                        #logging.info(f"Sell attempt aborted due to empty balance, returning response: {response}")
                        return response
                    qty = quantize_quantity(asset_free, step_size)
                    logging.info(f"[EXECUTE SPOT SELL] {symbol}: asset_free={asset_free}, sell_qty={qty}, step_size={step_size}, min_qty={min_qty}, min_notional={min_notional}")
                    is_valid, resp_dict, status = validate_order_qty(symbol, qty, price, min_qty, min_notional)
                    if not is_valid:
                        return resp_dict, status
                    
                    # Place the order after safeguards pass
                    return place_order_with_handling(symbol, side, qty, price, place_order_fn)
                
                except Exception as e:
                    logging.exception("Spot sell failed")
                    return {"error": f"Spot sell failed: {str(e)}"}, 500

            elif trade_type == "MARGIN":
                # Sell on margin account only. After sell, attempt to repay any borrowed USDT.
                # Cross-Margin SELL (long-only unwind).
                # IMPORTANT: Do NOT borrow the base asset to sell (no shorting).
                try:
                    before = snapshot_balances()

                    # TODO: use existing function _get_margin_free to get asset_free
                    acc = client.get_margin_account()
                    asset_free = Decimal("0")
                    for a in acc.get("userAssets", []):
                        if a.get("asset") == base_asset:
                            asset_free = Decimal(str(a.get("free", "0")))
                            break

                    if asset_free <= Decimal("0"):
                        logging.warning(f"No margin {base_asset} balance to sell. Aborting.")
                        return {"warning": f"No margin {base_asset} balance to sell. Aborting."}, 200

                    qty = quantize_quantity(asset_free, step_size)
                    logging.info(f"[EXECUTE MARGIN SELL] {symbol}: sell_qty={qty}")
                    is_valid, resp_dict, status = validate_order_qty(symbol, qty, price, min_qty, min_notional)
                    if not is_valid:
                        return resp_dict, status

                    # Place market margin sell (no auto-repay needed since only USDT is borrowed)
                    resp = client.create_margin_order(
                        symbol=symbol,
                        side="SELL",
                        type="MARKET",
                        quantity=float(qty),
                        sideEffectType="NO_SIDE_EFFECT",  # AUTO_REPAY
                        isIsolated=False
                    )

                    # After selling, attempt to repay any USDT debt using proceeds
                    try:
                        #TODO: check logic & also combine call _get_margin_free&_get_margin_debt to minimize api calls
                        # Refresh balances to get latest free USDT and debt
                        usdt_free_after = _get_margin_free("USDT")
                        usdt_debt = _get_margin_debt("USDT")
                        repay_amt = min(usdt_free_after, usdt_debt)
                        if repay_amt > Decimal("0"):
                            logging.info(f"[MARGIN AUTO-REPAY USDT] Repaying {repay_amt} USDT")
                            client.repay_margin_loan(asset="USDT", amount=float(repay_amt))
                    except Exception as e:
                        logging.warning(f"Post-sell USDT auto-repay failed: {e}")

                    logging.info(f"[MARGIN ORDER] {side} successfully executed: {qty} {symbol} at {price} on {datetime.now(timezone.utc).isoformat()}")
                    
                    after = snapshot_balances()
                    # Compare spot balances only
                    compare_spot_balances(before["spot"], after["spot"])

                    return {"status": "margin_sell_executed", "order": resp}, 200

                except BinanceAPIException as e:
                    logging.error(f"Binance API error during margin sell: {e.message}")
                    return {"error": f"Margin sell failed: {e.message}"}, 500
                except Exception as e:
                    logging.exception("Margin sell failed")
                    return {"error": f"Margin sell failed: {str(e)}"}, 500
            else:
                return {"error": f"Unknown trade type {trade_type}"}, 400

        # Unknown side (shouldn't happen)
        else:
            return {"error": f"Unknown side {side}. No action performed."}, 400

    except Exception as e:
        logging.exception("Trade execution failed")
        return {"error": f"Trade execution failed: {str(e)}"}, 500


# -------------------------
# Flask hooks and health endpoints
# -------------------------
@app.before_request
def check_ip_whitelist():
    if request.method == "POST" and request.path == WEBHOOK_REQUEST_PATH:
        raw_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        client_ip = raw_ip.split(",")[0].strip() # Take only the first IP in case there are multiple
        if client_ip not in TRADINGVIEW_IPS:
            logging.warning(f"Blocked request from unauthorized IP: {client_ip}")
            logging.warning(f"IP address owned by: https://ipapi.co/{client_ip}/json/")
            return jsonify({"error": f"IP {client_ip} not allowed"}), 403
        
@app.before_request
def before_req():
    if should_log_request():
        logging.info(f"[REQUEST] Method:'{request.method}', Path:'{request.path}'")

@app.after_request
def after_req(response):
    if should_log_request():
        logging.info(f"[RESPONSE] Method:'{request.method}', Path:'{request.path}' -> Status Code:'{response.status_code}'")
    return response
    
@app.route('/', methods=['GET', 'HEAD'])
def root():
    # logging.info(f"[ROOT] Call to root endpoint received.")
    # return '', 204
    return jsonify({"status": "rooty"}), 200

@app.route('/ping', methods=['GET'])
def ping():
    # logging.info("[PING] Keep-alive ping received.")
    return "pong", 200

@app.route('/health-check', methods=['GET', 'HEAD'])
def health_check():
    # logging.info("[HEALTH CHECK] Call to health-check endpoint received.")
    return jsonify({"status": "healthy"}), 200

@app.route('/healthz', methods=['GET', 'HEAD'])
def healthz():
    # logging.info("[HEALTHZ CHECK] Call to healthz endpoint received.")
    return jsonify({"status": "healthzy"}), 200


# -------------------------
# Webhook endpoint
# -------------------------
@app.route(WEBHOOK_REQUEST_PATH, methods=['POST'])
def webhook():
    log_webhook_delimiter("START")
    start_time = time.perf_counter()

    try:
        # JSON validation
        data, error_response = validate_json()
        if not data:
            return error_response
        
        # Secret validation
        valid_secret, error_response = validate_secret(data)
        if not valid_secret:
            return error_response

        # Log payload without secret
        data_for_log = {k: v for k, v in data.items() if k != SECRET_FIELD}
        logging.info(f"[WEBHOOK] Received payload: {data_for_log}")
        
        # Parse fields
        try:
            action = data.get("action", "").strip().upper()
            symbol = data.get("symbol", "").strip().upper()
            buy_pct_raw = data.get("buy_pct", None)
            amt_raw = data.get("amount", None)
            trade_type = data.get("type", "SPOT").strip().upper()  # MARGIN or SPOT
            leverage_raw = data.get("leverage", None)
        except Exception as e:
            logging.exception("Failed to extract fields")
            return jsonify({"error": "Invalid fields"}), 400

        log_parsed_payload(action, symbol, buy_pct_raw, amt_raw, trade_type, leverage_raw)

        # Easter egg check
        resp = detect_tradingview_placeholder(action)
        if resp:
            return resp
        
        # Validate action and symbol
        if action not in {"BUY", "BUY_BTC_SMALL", "SELL"}:
            logging.error(f"Invalid action: {action}")
            return jsonify({"error": "Invalid action"}), 400
        if symbol not in ALLOWED_SYMBOLS:
            logging.error(f"Symbol not allowed: {symbol}")
            return jsonify({"error": "Symbol not allowed"}), 400

        is_buy = action in {"BUY", "BUY_BTC_SMALL"}
        buy_pct, amt, error_response = validate_and_normalize_buy_fields(is_buy, buy_pct_raw, amt_raw)
        if error_response:
            return error_response

        result, status_code = execute_trade(
            symbol=symbol,
            side="BUY" if is_buy else "SELL",
            buy_pct=buy_pct if is_buy else None,
            amt=amt,
            trade_type=trade_type,
            leverage_raw=leverage_raw,
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
    """
    Detect if the action is still the raw TradingView placeholder.
    Returns a Flask response if placeholder is found, otherwise None.
    """
    if action == "{{STRATEGY.ORDER.ACTION}}":
        logging.warning("TradingView placeholder received instead of explicit action.")
        logging.warning("Did you accidentally paste {{strategy.order.action}} instead of letting TradingView expand it? Use BUY or SELL instead...")
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
