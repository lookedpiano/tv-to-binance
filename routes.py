import json
import logging
from flask import Blueprint, render_template, jsonify, request
from datetime import datetime
from zoneinfo import ZoneInfo
from binance_data import (
    get_redis,
    get_client,
    fetch_and_cache_balances,
    fetch_and_cache_filters,
    get_cached_orders,
    DAILY_BALANCE_SNAPSHOT_KEY,
)
from utils import should_log_request, load_ip_file, require_admin_key
from security import verify_before_request_secret
from config._settings import WEBHOOK_REQUEST_PATH, ALLOWED_SYMBOLS, INTERNAL_PUBLIC_ALERTS_SECRET

routes = Blueprint("routes", __name__)

# ==========================================================
# ========== TIMEZONE CONFIG ===============================
# ==========================================================
TZ = ZoneInfo("Europe/Zurich")

# ==========================================================
# ========== REQUEST HOOKS =================================
# ==========================================================

@routes.before_request
def enforce_ip_whitelist():
    """
    Restrict POST requests to the TradingView webhook endpoint.
    """
    if request.method != "POST" or request.path != WEBHOOK_REQUEST_PATH:
        return  # Skip for non-webhook routes

    raw_ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    client_ip = raw_ip.split(",")[0].strip()  # Take first IP if multiple

    try:
        allowed_ips = load_ip_file("config/tradingview_ips.txt")
    except Exception as e:
        logging.error(f"[SECURITY] Could not load IP allowlist: {e}")
        return jsonify({"error": "Server IP configuration error"}), 500

    if client_ip not in allowed_ips:
        logging.warning(f"[SECURITY] Blocked unauthorized IP: {client_ip}")
        logging.warning(f"[SECURITY] IP details: https://ipapi.co/{client_ip}/json/")
        return jsonify({"error": f"IP {client_ip} not allowed"}), 403


@routes.before_request
def log_request():
    """Log all incoming requests (if enabled)."""
    if should_log_request():
        logging.info(f"[REQUEST] {request.method} {request.path}")


@routes.before_request
def enforce_before_request_secret():
    """
    Enforces that this server instance is authorized by verifying
    BEFORE_REQUEST_SECRET matches BEFORE_REQUEST_SECRET_HASH.
    """
    if verify_before_request_secret():
        return  # Authorized -> allow request

    logging.warning(
        f"[SECURITY] Blocked unauthorized request to {request.path} "
        "(invalid BEFORE_REQUEST_SECRET)"
    )

    return jsonify({"error": "Unauthorized server instance"}), 401


@routes.after_request
def log_response(response):
    """Log response details (if enabled)."""
    if should_log_request():
        logging.info(f"[RESPONSE] {request.method} {request.path} → {response.status_code}")
    return response


# ==========================================================
# ========== HEALTH & ROOT ENDPOINTS ========================
# ==========================================================

@routes.route("/", methods=["GET", "HEAD"])
def root():
    return jsonify({"status": "rooty"}), 200


@routes.route("/ping", methods=["GET"])
def ping():
    return "pong", 200


@routes.route("/health-check", methods=["GET", "HEAD"])
@routes.route("/healthz", methods=["GET", "HEAD"])
def health_check():
    """General health probe endpoints."""
    if (unauthorized := require_admin_key()):
        return unauthorized
    return jsonify({"status": "healthy"}), 200


# ==========================================================
# ========== PRICE CACHE ENDPOINTS ==========================
# ==========================================================

@routes.route("/cache/prices", methods=["GET"])
def cache_prices():
    """Return all cached prices."""
    try:
        r = get_redis()
        snapshot = r.hgetall("price_cache")
        if not snapshot:
            return jsonify({"message": "No cached prices available"}), 200
        return jsonify(snapshot), 200
    except Exception as e:
        logging.error(f"[ROUTE] /cache/prices failed: {e}")
        return jsonify({"error": "Failed to fetch cached prices"}), 500


@routes.route("/cache/prices/count", methods=["GET"])
def cache_prices_count():
    """Return number of cached price entries."""
    try:
        r = get_redis()
        count = r.hlen("price_cache")
        return jsonify({"count": count}), 200
    except Exception as e:
        logging.error(f"[ROUTE] /cache/prices/count failed: {e}")
        return jsonify({"error": "Failed to count cached prices"}), 500


@routes.route("/cache/prices/<symbol>", methods=["GET"])
def cache_price_symbol(symbol):
    """Return cached price for a single symbol."""
    try:
        r = get_redis()
        price = r.hget("price_cache", symbol.upper())
        if price is None:
            return jsonify({"error": f"No cached price for {symbol.upper()}"}), 404
        return jsonify({symbol.upper(): price}), 200
    except Exception as e:
        logging.error(f"[ROUTE] /cache/prices/{symbol} failed: {e}")
        return jsonify({"error": "Failed to fetch cached price"}), 500


# ==========================================================
# ========== BALANCE & FILTER & ORDERS CACHE ENDPOINTS =====
# ==========================================================

@routes.route("/cache/balances", methods=["GET"])
def cache_balances():
    if (unauthorized := require_admin_key()):
        return unauthorized
    try:
        r = get_redis()
        raw = r.get("account_balances")
        return raw or "{}", 200, {"Content-Type": "application/json"}
    except Exception as e:
        logging.error(f"[ROUTE] /cache/balances failed: {e}")
        return jsonify({"error": "Failed to fetch balances"}), 500


@routes.route("/cache/filters", methods=["GET"])
def cache_all_filters():
    """Return all cached symbol filters."""
    try:
        r = get_redis()
        keys = r.keys("filters:*")
        if not keys:
            return jsonify({"message": "No cached filters found"}), 200
        data = {k.split("filters:")[1]: json.loads(v) for k in keys if (v := r.get(k))}
        return jsonify(data), 200
    except Exception as e:
        logging.error(f"[ROUTE] /cache/filters failed: {e}")
        return jsonify({"error": "Failed to fetch filters"}), 500


@routes.route("/cache/filters/<symbol>", methods=["GET"])
def cache_filters(symbol):
    """Return cached filters for a specific symbol."""
    try:
        r = get_redis()
        raw = r.get(f"filters:{symbol.upper()}")
        return raw or "{}", 200, {"Content-Type": "application/json"}
    except Exception as e:
        logging.error(f"[ROUTE] /cache/filters/{symbol} failed: {e}")
        return jsonify({"error": "Failed to fetch symbol filters"}), 500


@routes.route("/cache/refresh/balances", methods=["POST"])
def refresh_balances():
    if (unauthorized := require_admin_key()):
        return unauthorized
    try:
        client = get_client()
        fetch_and_cache_balances(client, "API")
        return jsonify({"message": "Balances refreshed successfully"}), 200
    except Exception as e:
        logging.exception("[ROUTE] /cache/refresh/balances failed")
        return jsonify({"error": f"Failed to refresh balances: {e}"}), 500


@routes.route("/cache/refresh/filters", methods=["POST"])
def refresh_filters():
    if (unauthorized := require_admin_key()):
        return unauthorized
    try:
        client = get_client()
        fetch_and_cache_filters(client, ALLOWED_SYMBOLS, "API")
        return jsonify({"message": "Filters refreshed successfully"}), 200
    except Exception as e:
        logging.exception("[ROUTE] /cache/refresh/filters failed")
        return jsonify({"error": f"Failed to refresh filters: {e}"}), 500


@routes.route("/cache/orders", methods=["GET"])
def cache_orders():
    """Return recent cached order logs."""
    if (unauthorized := require_admin_key()):
        return unauthorized
    try:
        limit = int(request.args.get("limit", 100))
        orders = get_cached_orders(limit)
        return jsonify({"count": len(orders), "orders": orders}), 200
    except Exception as e:
        logging.exception("[ROUTE] /cache/orders failed")
        return jsonify({"error": f"Failed to fetch cached orders: {e}"}), 500


# ==========================================================
# ========== DAILY BALANCE SNAPSHOT ========================
# ==========================================================

@routes.route("/cache/balance-snapshots", methods=["GET"])
def get_balance_snapshots():
    """Return all stored daily balance snapshots."""
    if (unauthorized := require_admin_key()):
        return unauthorized
    try:
        r = get_redis()
        snapshots = r.hgetall(DAILY_BALANCE_SNAPSHOT_KEY)
        parsed = sorted(
            [json.loads(v) for v in snapshots.values()],
            key=lambda s: s["date"]
        )
        return jsonify({"count": len(parsed), "snapshots": parsed}), 200
    except Exception as e:
        logging.exception("[ROUTE] /cache/balance-snapshots failed")
        return jsonify({"error": f"Failed to fetch balance snapshots: {e}"}), 500

@routes.route("/cache/balance-snapshots/count", methods=["GET"])
def cache_balance_snapshots_count():
    """Return number of cached balance-snapshots entries."""
    if (unauthorized := require_admin_key()):
        return unauthorized
    try:
        r = get_redis()
        count = r.hlen(DAILY_BALANCE_SNAPSHOT_KEY)
        return jsonify({"count": count}), 200
    except Exception as e:
        logging.error(f"[ROUTE] /cache/balance-snapshots/count failed: {e}")
        return jsonify({"error": "Failed to count cached balance-snapshots"}), 500


# ==========================================================
# ========== CACHE SUMMARY ENDPOINT =========================
# ==========================================================

@routes.route("/cache/summary", methods=["GET"])
def cache_summary():
    """
    Return a summary overview of the current Redis cache state.
    Useful for monitoring dashboards or health checks.
    """
    try:
        r = get_redis()

        summary = {
            "prices": {
                "count": r.hlen("price_cache"),
            },
            "balances": {
                "exists": bool(r.exists("account_balances")),
            },
            "filters": {
                "count": len(r.keys("filters:*")),
            },
        }

        return jsonify(summary), 200

    except Exception as e:
        logging.error(f"[ROUTE] /cache/summary failed: {e}")
        return jsonify({"error": "Failed to fetch cache summary"}), 500


# ==========================================================
# ========== ORIGINAL CACHE ENDPOINT =======================
# ==========================================================
@routes.route("/public/alerts", methods=["GET"])
def public_alerts():
    """
    Returns ALL daily Larsson alerts only to authenticated internal clients.
    """
    try:
        client_secret = request.headers.get("X-Public-Auth")

        logging.info('well...')
        logging.info(client_secret)
        booleooon = not client_secret or client_secret != INTERNAL_PUBLIC_ALERTS_SECRET
        logging.info(booleooon)

        if not client_secret or client_secret != INTERNAL_PUBLIC_ALERTS_SECRET:
            logging.warning("[PUBLIC ALERTS] Unauthorized attempt")
            return jsonify({"error": "Unauthorized"}), 401

        r = get_redis()
        keys = sorted(r.keys("larsson_alert:*"))
        alerts = []

        for key in keys:
            raw = r.get(key)
            if raw:
                alerts.append(json.loads(raw))

        return jsonify({"alerts": alerts}), 200

    except Exception as e:
        logging.error(f"[ROUTE] /public/alerts failed: {e}")
        return jsonify({"error": "Failed to fetch alerts"}), 500


# ==========================================================
# ========== DASHBOARD======================================
# ==========================================================
@routes.route("/dashboard", methods=["GET"])
def dashboard():
    if (unauthorized := require_admin_key()):
        return unauthorized
    try:
        r = get_redis()

        prices = r.hgetall("price_cache")
        balances_raw = r.get("account_balances")
        balances = json.loads(balances_raw)["balances"] if balances_raw else {}

        filters_count = len(r.keys("filters:*"))

        ts_bal = r.get("last_refresh_balances")
        ts_filt = r.get("last_refresh_filters")
        ts_prices = r.get("last_refresh_prices")

        last_balances = (datetime.fromtimestamp(float(ts_bal), TZ).strftime("%Y-%m-%d %H:%M:%S") if ts_bal else "Never")
        last_filters = (datetime.fromtimestamp(float(ts_filt), TZ).strftime("%Y-%m-%d %H:%M:%S") if ts_filt else "Never")
        last_prices = (datetime.fromtimestamp(float(ts_prices), TZ).strftime("%Y-%m-%d %H:%M:%S") if ts_prices else "Never")

        return render_template(
            "dashboard.html",
            balances=balances,
            prices=prices,
            filters_count=filters_count,
            last_balances=last_balances,
            last_filters=last_filters,
            last_prices=last_prices
        )

    except Exception as e:
        logging.exception("[ROUTE] /dashboard failed")
        return jsonify({"error": f"Failed to render dashboard: {e}"}), 500
