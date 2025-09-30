# config/settings.py

# Types allowed for trading
ALLOWED_TRADE_TYPES = {"SPOT"}

# Symbols allowed for trading
ALLOWED_SYMBOLS = {
    # USDT pairs
    "BTCUSDT", "ETHUSDT", "ADAUSDT", "DOGEUSDT",
    "PEPEUSDT", "XRPUSDT", "WIFUSDT", "BNBUSDT", "SOLUSDT",

    # USDC pairs
    "BTCUSDC", "ETHUSDC", "ADAUSDC", "DOGEUSDC",
    "PEPEUSDC", "XRPUSDC", "WIFUSDC", "BNBUSDC", "SOLUSDC"
}

# Payload fields
ALLOWED_FIELDS = {
    "action",
    "symbol",
    "buy_pct",
    "buy_amount",
    "sell_pct",
    "sell_amount",
    "type",
    "leverage",
    "client_secret"
}

REQUIRED_FIELDS = {"action", "symbol", "client_secret"}

SECRET_FIELD = "client_secret"
WEBHOOK_REQUEST_PATH = "/to-the-moon"

# Safeguards
MAX_CROSS_LEVERAGE = 3
