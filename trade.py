import time
import logging
from decimal import Decimal

from binance_data import (
    refresh_balances_for_assets,
    log_order_to_cache,
    get_client,
)

from validation import (
    validate_order_qty,
)

from utils import (
    split_symbol,
    quantize_quantity,
    sanitize_filters,
)

from exchange import (
    get_current_price,
    get_symbol_filters,
    get_balances,
    resolve_trade_amount,
    place_order_with_handling,
)

# ---------------------------------
# Unified trade execution
# ---------------------------------
def execute_trade(
    symbol: str,
    side: str,
    pct=None,
    amt=None,
    amt_in_crypto=False,
    amt_in_funds=False,
    trade_type="SPOT",
    place_order_fn=None,
):
    """
    Unified trade executor for SPOT; handles buy/sell, quantity math, filter validation, and order placement.
    """
    try:
        logging.info(f"[EXECUTE] side={side}, pct={pct}, amt={amt}, amt_in_crypto={amt_in_crypto}, amt_in_funds={amt_in_funds}")

        # === 1. Price retrieval (with one retry) ===
        price = get_current_price(symbol)
        if price is None:
            logging.info(f"[EXECUTE] Retrying price fetch for {symbol} in 3s...")
            time.sleep(3)
            price = get_current_price(symbol)
        if price is None:
            message = f"No price available for {symbol}. Aborting trade."
            logging.warning(f"[EXECUTE] {message}")
            try:
                log_order_to_cache(symbol, side or "?", "?", "?",status="error", message=message)
            except Exception as e:
                logging.warning(f"[ORDER LOG] Failed to log missing price error: {e}")
            return {"error": message}, 200

        # === 2. Fetch filters ===
        filters = get_symbol_filters(symbol)
        if not filters:
            message = f"Filters unavailable for {symbol}"
            logging.warning(f"[EXECUTE] {message}")
            try:
                log_order_to_cache(symbol, side or "?", "?", price,status="error", message=message)
            except Exception as e:
                logging.warning(f"[ORDER LOG] Failed to log missing filters error: {e}")
            return {"error": message}, 200

        filters = sanitize_filters(filters)

        step_size = Decimal(filters.get("step_size", "0"))
        min_qty = Decimal(filters.get("min_qty", "0"))
        min_notional = Decimal(filters.get("min_notional", "0"))
        if not all([step_size, min_qty, min_notional]):
            message = (
                f"Incomplete filters for {symbol}: "
                f"step_size={step_size}, min_qty={min_qty}, min_notional={min_notional}"
            )
            logging.warning(f"[EXECUTE] {message}")
            try:
                log_order_to_cache(symbol, side or "?", "?", price,status="error", message=message)
            except Exception as e:
                logging.warning(f"[ORDER LOG] Failed to log incomplete filters error: {e}")
            return {"error": message}, 200

        # === 3. Determine assets ===
        try:
            base_asset, quote_asset = split_symbol(symbol)
        except ValueError as e:
            message = f"Failed to parse base/quote assets for {symbol}: {e}"
            logging.error(f"[EXECUTE] {message}")
            try:
                log_order_to_cache(symbol, side or "?", "?", price,status="error", message=message)
            except Exception as log_err:
                logging.warning(f"[ORDER LOG] Failed to log symbol-parse error: {log_err}")
            return {"error": message}, 400

        # === 4. Determine balance and target amount ===
        if side == "BUY":
            balance_asset = quote_asset
        elif side == "SELL":
            balance_asset = base_asset
        else:
            message = f"Unknown side {side}. Must be BUY or SELL."
            logging.error(f"[EXECUTE] {message}")
            try:
                log_order_to_cache(symbol, side or "?", "?", price,status="error", message=message)
            except Exception as e:
                logging.warning(f"[ORDER LOG] Failed to log invalid side error: {e}")
            return {"error": message}, 400
        
        balances = get_balances() or {}
        free_balance = balances.get(balance_asset, Decimal("0"))
        if free_balance <= 0:
            message = f"No available {balance_asset} balance to {side.lower()}."
            logging.warning(f"[EXECUTE] {message}")
            try:
                log_order_to_cache(symbol, side, "?", price,status="error", message=message)
            except Exception as e:
                logging.warning(f"[ORDER LOG] Failed to log balance error: {e}")
            return {"warning": message}, 200

        # Resolve amount
        target_amount, error_msg = resolve_trade_amount(
            symbol=symbol,
            side=side,
            free_balance=free_balance,
            amt=amt,
            pct=pct,
            price=price,
            amt_in_crypto=amt_in_crypto,
            amt_in_funds=amt_in_funds,
        )
        if error_msg:
            return {"error": error_msg}, 200

        # === 5. Compute quantity ===
        # target_amount here may refer to base or quote, depending on flags
        if side == "BUY":
            if amt_in_crypto:
                # User specified base asset directly, e.g. buy 1.2 ETH
                raw_qty = amt
                notional = raw_qty * price
                logging.info(f"[BUY:CRYPTO-AMOUNT] qty={raw_qty} ({notional:.2f} quote value)")
            else:
                # Normal path: buy_funds_amount or buy_funds_pct (in quote)
                raw_qty = target_amount / price
                notional = target_amount
                logging.info(f"[BUY:FUNDS-{('PCT' if pct else 'AMT')}] notional≈{notional:.2f}, qty={raw_qty}")

        elif side == "SELL":
            if amt_in_funds:
                # User specified desired quote amount, e.g. sell BTC worth 100 USDT
                raw_qty = amt / price
                notional = amt
                logging.info(f"[SELL:FUNDS-AMOUNT] notional≈{notional:.2f}, qty={raw_qty}")
            else:
                # Normal path: sell_crypto_amount or sell_crypto_pct (in base)
                raw_qty = target_amount
                notional = raw_qty * price
                logging.info(f"[SELL:CRYPTO-{('PCT' if pct else 'AMT')}] qty={raw_qty}, notional≈{notional:.2f}")
        else:
            return {"error": f"Unknown side {side}"}, 400

        qty = quantize_quantity(raw_qty, step_size)
        notional = qty * price

        # === 6. Log trade intent ===
        action_label = "BUY" if side == "BUY" else "SELL"
        logging.info(f"[EXECUTE {action_label}] {symbol}: qty={qty}, price={price}, notional≈{notional:.2f}")
        logging.debug(f"[DETAILS] step_size={step_size}, min_qty={min_qty}, min_notional={min_notional}")

        # === 7. Validate filters ===
        is_valid, resp_dict, http_status = validate_order_qty(symbol, qty, price, min_qty, min_notional, side)
        if not is_valid:
            return resp_dict, http_status

        # === 8. Place order ===
        result, order_http_status = place_order_with_handling(symbol, side, qty, price, place_order_fn)

        # === 9. Determine outcome and refresh balances if trade succeeded ===
        if order_http_status == 200 and result and "error" not in result:
            order_status = "success"
            message = f"Order executed successfully ({symbol} {side})"
            try:
                client = get_client()
                refresh_balances_for_assets(client, [base_asset, quote_asset])
            except Exception as e:
                logging.warning(f"[CACHE] Post-trade balance refresh failed: {e}")
        else:
            order_status = "error"
            message = result.get("error", "Unknown failure") if isinstance(result, dict) else str(result)

        # === 10. Log order attempt ===
        try:
            log_order_to_cache(symbol, side, qty, price, order_status, message)
        except Exception as e:
            logging.warning(f"[ORDER LOG] Failed to log order: {e}")

        return result, order_http_status

    except Exception as e:
        logging.exception(f"[EXECUTE] Trade execution failed for {symbol}")
        return {"error": f"Trade execution failed: {str(e)}"}, 500
