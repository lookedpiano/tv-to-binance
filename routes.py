import json
import logging
from flask import Blueprint, render_template, jsonify, request
from datetime import datetime
from zoneinfo import ZoneInfo
from binance_data import _get_redis, get_client, fetch_and_cache_balances, fetch_and_cache_filters, get_cached_orders
from utils import should_log_request, load_ip_file
from config._settings import WEBHOOK_REQUEST_PATH, ADMIN_API_KEY, ALLOWED_SYMBOLS

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
    return jsonify({"status": "healthy"}), 200


# ==========================================================
# ========== PRICE CACHE ENDPOINTS ==========================
# ==========================================================

@routes.route("/cache/prices", methods=["GET"])
def cache_prices():
    """Return all cached prices."""
    try:
        r = _get_redis()
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
        r = _get_redis()
        count = r.hlen("price_cache")
        return jsonify({"count": count}), 200
    except Exception as e:
        logging.error(f"[ROUTE] /cache/prices/count failed: {e}")
        return jsonify({"error": "Failed to count cached prices"}), 500


@routes.route("/cache/prices/<symbol>", methods=["GET"])
def cache_price_symbol(symbol):
    """Return cached price for a single symbol."""
    try:
        r = _get_redis()
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
    provided_key = request.headers.get("X-Admin-Key")

    if not ADMIN_API_KEY or provided_key != ADMIN_API_KEY:
        logging.warning("[SECURITY] Unauthorized attempt to access /cache/balances")
        return jsonify({"error": "Unauthorized"}), 401

    try:
        r = _get_redis()
        raw = r.get("account_balances")
        return raw or "{}", 200, {"Content-Type": "application/json"}
    except Exception as e:
        logging.error(f"[ROUTE] /cache/balances failed: {e}")
        return jsonify({"error": "Failed to fetch balances"}), 500


@routes.route("/cache/filters", methods=["GET"])
def cache_all_filters():
    """Return all cached symbol filters."""
    try:
        r = _get_redis()
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
        r = _get_redis()
        raw = r.get(f"filters:{symbol.upper()}")
        return raw or "{}", 200, {"Content-Type": "application/json"}
    except Exception as e:
        logging.error(f"[ROUTE] /cache/filters/{symbol} failed: {e}")
        return jsonify({"error": "Failed to fetch symbol filters"}), 500


@routes.route("/cache/refresh/balances", methods=["POST"])
def refresh_balances():
    provided_key = request.headers.get("X-Admin-Key")
    if not ADMIN_API_KEY or provided_key != ADMIN_API_KEY:
        logging.warning("[SECURITY] Unauthorized attempt to refresh balances")
        return jsonify({"error": "Unauthorized"}), 401

    try:
        client = get_client()
        fetch_and_cache_balances(client)
        return jsonify({"message": "Balances refreshed successfully"}), 200
    except Exception as e:
        logging.exception("[ROUTE] /cache/refresh/balances failed")
        return jsonify({"error": f"Failed to refresh balances: {e}"}), 500


@routes.route("/cache/refresh/filters", methods=["POST"])
def refresh_filters():
    provided_key = request.headers.get("X-Admin-Key")
    if not ADMIN_API_KEY or provided_key != ADMIN_API_KEY:
        logging.warning("[SECURITY] Unauthorized attempt to refresh filters")
        return jsonify({"error": "Unauthorized"}), 401

    try:
        client = get_client()
        fetch_and_cache_filters(client, ALLOWED_SYMBOLS)
        return jsonify({"message": "Filters refreshed successfully"}), 200
    except Exception as e:
        logging.exception("[ROUTE] /cache/refresh/filters failed")
        return jsonify({"error": f"Failed to refresh filters: {e}"}), 500


@routes.route("/cache/orders", methods=["GET"])
def cache_orders():
    """Return recent cached order logs."""
    provided_key = request.headers.get("X-Admin-Key")
    if not ADMIN_API_KEY or provided_key != ADMIN_API_KEY:
        logging.warning("[SECURITY] Unauthorized attempt to access /cache/orders")
        return jsonify({"error": "Unauthorized"}), 401

    try:
        limit = int(request.args.get("limit", 100))
        orders = get_cached_orders(limit)
        return jsonify({"count": len(orders), "orders": orders}), 200
    except Exception as e:
        logging.exception("[ROUTE] /cache/orders failed")
        return jsonify({"error": f"Failed to fetch cached orders: {e}"}), 500


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
        r = _get_redis()

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
# ========== DASHBOARD======================================
# ==========================================================
@routes.route("/dashboard", methods=["GET"])
def dashboard():
    provided_key = request.headers.get("X-Admin-Key") or request.args.get("key")
    if not ADMIN_API_KEY or provided_key != ADMIN_API_KEY:
        logging.warning("[SECURITY] Unauthorized attempt to access the dashboard")
        return jsonify({"error": "Unauthorized"}), 401

    try:
        r = _get_redis()

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
