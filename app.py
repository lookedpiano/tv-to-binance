from flask import Flask
import logging

# Redis and WebSocket price cache and background_cache
from binance_data import (
    init_redis,
    init_client,
    start_ws_price_cache,
    start_background_cache,
)

from routes import routes
from webhook import webhook

# -------------------------
# Configuration
# -------------------------
from config._settings import (
    ALLOWED_SYMBOLS,
    BINANCE_API_KEY,
    BINANCE_SECRET_KEY,
    PORT,
    REDIS_URL,
)

# -------------------------
# Logging configuration
# -------------------------
logging.basicConfig(
    level=logging.INFO,
    format='[%(levelname)s] %(message)s'
)

app = Flask(__name__)
app.register_blueprint(routes)
app.register_blueprint(webhook)

# -------------------------
# CLIENT INIT
# -------------------------
client = init_client(BINANCE_API_KEY, BINANCE_SECRET_KEY)

# -------------------------
# REDIS + WS INIT
# -------------------------
try:
    init_redis(REDIS_URL)
    start_ws_price_cache(ALLOWED_SYMBOLS)
    start_background_cache(ALLOWED_SYMBOLS)
    logging.info("[INIT] Background caches initialized successfully.")
except Exception as e:
    logging.exception(f"[INIT] Failed to initialize background caches: {e}")

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
