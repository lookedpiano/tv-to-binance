import json
import logging
from flask import Blueprint, jsonify, request
from datetime import datetime
from binance_data import _get_redis, get_client, fetch_and_cache_balances, fetch_and_cache_filters
from utils import should_log_request, load_ip_file
from config._settings import WEBHOOK_REQUEST_PATH, ADMIN_API_KEY, ALLOWED_SYMBOLS

routes = Blueprint("routes", __name__)

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
# ========== BALANCE & FILTER CACHE ENDPOINTS ===============
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

        # Load cache summaries
        prices = r.hgetall("price_cache")
        balances_raw = r.get("account_balances")
        balances = json.loads(balances_raw)["balances"] if balances_raw else {}

        filters_count = len(r.keys("filters:*"))

        ts_bal = r.get("last_refresh_balances")
        ts_filt = r.get("last_refresh_filters")
        ts_prices = r.get("last_refresh_prices")

        last_balances = datetime.fromtimestamp(float(ts_bal)).strftime("%Y-%m-%d %H:%M:%S") if ts_bal else "Never"
        last_filters = datetime.fromtimestamp(float(ts_filt)).strftime("%Y-%m-%d %H:%M:%S") if ts_filt else "Never"
        last_prices = datetime.fromtimestamp(float(ts_prices)).strftime("%Y-%m-%d %H:%M:%S") if ts_prices else "Never"

        # Simple HTML dashboard
        html = f"""
        <html>
        <head>
            <title>Binance Cache Dashboard</title>
            <link rel="icon" type="image/png" href="/static/bitcoin_fav.png">
            <style>
                @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');

                body {{
                    font-family: 'Inter', sans-serif;
                    background: #111;
                    color: #eee;
                    margin: 40px;
                }}
                h1 {{
                    font-size: 2.6rem;
                    font-weight: 700;
                    letter-spacing: 0.5px;
                    text-align: center;
                    margin-bottom: 40px;
                }}
                .white-part {{
                    color: #fff;
                }}
                .orange-part {{
                    color: #ff6600;
                }}
                .section {{ margin-bottom: 2rem; }}
                button {{
                    background: #ff6600;
                    color: #111;
                    border: none;
                    padding: 10px 20px;
                    cursor: pointer;
                    font-weight: bold;
                    border-radius: 8px;
                    transition: background 0.2s ease;
                }}
                button:hover {{ background: #ff8533; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
                th, td {{ padding: 6px 10px; border-bottom: 1px solid #333; text-align: left; }}
                .time {{ color: #999; }}

                /* ---- Overlay / Loading ---- */
                #overlay {{
                    position: fixed;
                    top: 0; left: 0;
                    width: 100%; height: 100%;
                    background: rgba(0, 0, 0, 0.75);
                    display: none;
                    justify-content: center;
                    align-items: center;
                    z-index: 9999;
                }}
                #overlay-box {{
                    background: #222;
                    padding: 30px 40px;
                    border-radius: 12px;
                    text-align: center;
                    box-shadow: 0 0 20px rgba(0,0,0,0.5);
                }}
                #overlay-text {{
                    color: #ff6600;
                    font-weight: 600;
                    margin-top: 12px;
                    font-size: 1.1rem;
                }}
                .spinner {{
                    border: 4px solid #444;
                    border-top: 4px solid #ff6600;
                    border-radius: 50%;
                    width: 40px;
                    height: 40px;
                    animation: spin 1s linear infinite;
                    margin: 0 auto;
                }}
                @keyframes spin {{
                    0%   {{ transform: rotate(0deg); }}
                    100% {{ transform: rotate(360deg); }}
                }}
            </style>
        </head>
        <body>
            <h1>
                <span class="white-part">21 Mio&nbsp;|&nbsp;</span>
                <span class="orange-part">Friedrich Bitcoin Consulting</span>
            </h1>

            <div class="section">
                <h2>Balances</h2>
                <button onclick="refresh('balances')">Refresh Balances</button>
                <p class="time">Last refreshed: <b>{last_balances}</b></p>
                <table>
                    <tr><th>Asset</th><th>Free</th></tr>
                    {''.join(f'<tr><td>{k}</td><td>{v}</td></tr>' for k,v in balances.items())}
                </table>
            </div>

            <div class="section">
                <h2>Filters</h2>
                <p>Total cached filters: {filters_count}</p>
                <button onclick="refresh('filters')">Refresh Filters</button>
                <p class="time">Last refreshed: <b>{last_filters}</b></p>
            </div>

            <div class="section">
                <h2>Prices</h2>
                <p>Total cached prices: {len(prices)}</p>
                <button onclick="refresh('prices')">Refresh Prices</button>
                <p class="time">Last updated: <b>{last_prices}</b></p>
                <table>
                    <tr><th>Symbol</th><th>Price</th></tr>
                    {''.join(f'<tr><td>{k}</td><td>{v}</td></tr>' for k,v in list(prices.items())[:30])}
                </table>
                <p style="color:#666">(Showing up to 30 symbols)</p>
            </div>

            <!-- Overlay -->
            <div id="overlay">
                <div id="overlay-box">
                    <div class="spinner"></div>
                    <p id="overlay-text">Please wait…</p>
                </div>
            </div>

            <script>
                async function refresh(type) {{
                    const overlay = document.getElementById('overlay');
                    const overlayText = document.getElementById('overlay-text');
                    overlayText.textContent = `Refreshing ${{type}}…`;
                    overlay.style.display = 'flex'; // Show overlay immediately

                    const params = new URLSearchParams(window.location.search);
                    const key = params.get('key');
                    let url = "";

                    if (type === "balances") {{
                        url = "/cache/refresh/balances";
                    }} else if (type === "filters") {{
                        url = "/cache/refresh/filters";
                    }} else if (type === "prices") {{
                        url = "/cache/prices";
                    }} else {{
                        alert("Unknown refresh type");
                        overlay.style.display = 'none';
                        return;
                    }}

                    const opts = (type === "prices")
                        ? {{ method: "GET" }}
                        : {{ method: "POST", headers: key ? {{ "X-Admin-Key": key }} : {{}} }};

                    try {{
                        const resp = await fetch(url, opts);
                        const text = await resp.text();
                        let data;
                        try {{ data = JSON.parse(text); }} catch (e) {{ data = {{ raw: text }}; }}

                        overlay.style.display = 'none';  // Hide overlay after fetch completes

                        if (!resp.ok) {{
                            alert((data && (data.error || data.message)) || `HTTP {{resp.status}}`);
                            return;
                        }}

                        if (type === "prices") {{
                            alert("Prices fetched from cache.");
                        }} else {{
                            alert(data.message || data.error || "Done");
                        }}
                        location.reload();
                    }} catch (err) {{
                        overlay.style.display = 'none';
                        alert("Refresh failed: " + err);
                    }}
                }}
            </script>
        </body>
        </html>
        """
        return html, 200
    except Exception as e:
        logging.exception("[ROUTE] /dashboard failed")
        return jsonify({"error": f"Failed to render dashboard: {e}"}), 500
