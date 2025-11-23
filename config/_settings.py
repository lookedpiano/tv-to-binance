import os

# -------------------------
# Types allowed for trading
# -------------------------
ALLOWED_TRADE_TYPES = {"SPOT"}

# -------------------------
# Symbols allowed for trading
# -------------------------
ALLOWED_SYMBOLS = [
    # USDT pairs
    "BTCUSDT", "ETHUSDT", "ADAUSDT", "DOGEUSDT", "ONDOUSDT",
    "PEPEUSDT", "XRPUSDT", "WIFUSDT", "BNBUSDT", "SOLUSDT",
    "TRXUSDT", "ZECUSDT",

    # USDC pairs
    "BTCUSDC", "ETHUSDC", "ADAUSDC", "DOGEUSDC", "ONDOUSDC",
    "PEPEUSDC", "XRPUSDC", "WIFUSDC", "BNBUSDC", "SOLUSDC",
    "TRXUSDC", "ZECUSDC",

    # Crypto pairs
    "ETHBTC",
    "ADABTC", "ADAETH",
    "SOLBTC", "SOLETH",
    "ZECBTC", "ZECETH"
]

# -------------------------
# Known quote assets
# -------------------------
KNOWN_QUOTES = (
    "USDT",
    "USDC",
    "BTC",
    "ETH",
    "BNB",
    "XRP",
    "SOL",
    "TRX",
    "DOGE",
    "ADA",
    "ZEC"
)

# -------------------------
# Payload fields
# -------------------------
ALLOWED_FIELDS = {
    "action",
    "symbol",
    "buy_quote_pct",
    "buy_quote_amount",
    "buy_base_amount",
    "sell_base_pct",
    "sell_base_amount",
    "sell_quote_amount",
    "type",
    "leverage",
    "client_secret"
}

REQUIRED_FIELDS = {"action", "symbol", "client_secret"}

SECRET_FIELD = "client_secret"
WEBHOOK_REQUEST_PATH = "/to-the-moon"

# -------------------------
# Safeguards
# -------------------------
MAX_CROSS_LEVERAGE = 3

# -------------------------
# Helper
# -------------------------
def _get_bool_env(var_name: str, default: bool = False) -> bool:
    val = os.getenv(var_name)
    if val is None:
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "on")

# -------------------------
# Environment variables
# -------------------------
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY")
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
REDIS_URL = os.getenv("REDIS_URL")
SKIP_INITIAL_FETCH = _get_bool_env("SKIP_INITIAL_FETCH", default=False)
GENERATE_FAKE_BALANCE_DATA = _get_bool_env("GENERATE_FAKE_BALANCE_DATA", default=False)
PORT = os.getenv("PORT", "4747")

if not ADMIN_API_KEY:
    raise RuntimeError("Missing required environment variable: ADMIN_API_KEY")
if not BINANCE_API_KEY:
    raise RuntimeError("Missing required environment variable: BINANCE_API_KEY")
if not BINANCE_SECRET_KEY:
    raise RuntimeError("Missing required environment variable: BINANCE_SECRET_KEY")
if not WEBHOOK_SECRET:
    raise RuntimeError("Missing required environment variable: WEBHOOK_SECRET")
if not REDIS_URL:
    raise RuntimeError("Missing required environment variable: REDIS_URL")
if not PORT:
    raise RuntimeError(
        "Missing required environment variable: PORT.\n"
        "The following ports are reserved by Render and cannot be used: 18012, 18013 and 19099.\n"
        "Choose a port such that: 1024 < PORT <= 49000, excluding the reserved ones."
    )
