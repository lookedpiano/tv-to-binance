import debug_suppression  # modifies logging settings (logging suppression + exception filtering)
import logging

from flask import Flask
from binance_data import init_all, apply_api_delay
from routes import routes
from webhook import webhook
from config._settings import PORT

logging.basicConfig(
    level=logging.INFO,
    format='[%(levelname)s] %(message)s'
)

app = Flask(__name__)
app.register_blueprint(routes)
app.register_blueprint(webhook)

# -------------------------
# INIT
# -------------------------
try:
    apply_api_delay()

    init_all()
except Exception as e:
    logging.exception(f"[INIT] Failed to initialize background services: {e}")

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
