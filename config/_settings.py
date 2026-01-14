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
    "TRXUSDT", "ZECUSDT", "ICPUSDT", "PAXGUSDT", "DASHUSDT",
    "STRKUSDT", "ASTERUSDT", "AAVEUSDT", "ACTUSDT", "ACXUSDT",
    "AIXBTUSDT", "ALGOUSDT", "API3USDT", "APTUSDT", "ARUSDT",
    "ARBUSDT", "ARKMUSDT", "ATOMUSDT", "AVAXUSDT", "AXSUSDT",
    "BANANAUSDT", "BCHUSDT", "TNSRUSDT", "BEAMXUSDT", "BONKUSDT",
    "CAKEUSDT", "CFXUSDT", "CGPTUSDT", "CHZUSDT", "COOKIEUSDT",
    "COTIUSDT", "CRVUSDT", "DOTUSDT", "DYDXUSDT", "EGLDUSDT",
    "ENAUSDT", "ENJUSDT", "ENSUSDT", "ETCUSDT", "FETUSDT",
    "FILUSDT", "FLOKIUSDT", "FLUXUSDT", "GALAUSDT", "GMTUSDT",
    "GRTUSDT", "HBARUSDT", "IDEXUSDT", "ILVUSDT", "IMXUSDT",
    "INJUSDT", "IOUSDT", "JTOUSDT", "JUPUSDT", "KMNOUSDT",
    "LDOUSDT", "LINKUSDT", "LPTUSDT", "LSKUSDT", "LTCUSDT",
    "MANTAUSDT", "MASKUSDT", "MINAUSDT", "NEARUSDT", "NEOUSDT",
    "NMRUSDT", "OMUSDT", "OPUSDT", "ORCAUSDT", "PARTIUSDT",
    "PENDLEUSDT", "PHAUSDT", "PIXELUSDT", "POLUSDT", "PYTHUSDT",
    "QNTUSDT", "RAYUSDT", "RENDERUSDT", "ROSEUSDT", "RUNEUSDT",
    "SUSDT", "SANDUSDT", "SEIUSDT", "SHIBUSDT", "SNXUSDT",
    "STXUSDT", "SUIUSDT", "SUSHIUSDT", "TAOUSDT", "THEUSDT",
    "THETAUSDT", "TIAUSDT", "TONUSDT", "TRBUSDT", "TRUMPUSDT",
    "TURBOUSDT", "UMAUSDT", "UNIUSDT", "UTKUSDT", "VETUSDT",
    "VIRTUALUSDT", "WUSDT", "WLDUSDT", "XLMUSDT", "XTZUSDT",
    "YGGUSDT", "ZKUSDT", "ZROUSDT", "WLFIUSDT",
    "BROCCOLI714USDT", "GUNUSDT", "BERAUSDT", "HUMAUSDT",
    "BLURUSDT",
    
    # no USDC pairs
    "XNOUSDT", "BATUSDT", "DUSKUSDT", "GLMUSDT", "AUDIOUSDT",
    "AXLUSDT", "BICOUSDT", "BNSOLUSDT", "BTTCUSDT", "C98USDT",
    "CTKUSDT", "DATAUSDT", "DODOUSDT", "FIDAUSDT", "FLOWUSDT",
    "FXSUSDT", "GLMRUSDT", "HFTUSDT", "HOOKUSDT", "IQUSDT",
    "JASMYUSDT", "JOEUSDT", "KNCUSDT", "LUNAUSDT", "MANAUSDT",
    "MOVRUSDT", "NEXOUSDT", "NTRNUSDT", "POLYXUSDT", "PONDUSDT",
    "PYRUSDT", "RLCUSDT", "RONINUSDT", "SCRUSDT", "SPELLUSDT",
    "SUPERUSDT", "TFUELUSDT", "VTHOUSDT", "WOOUSDT", "XECUSDT",
    "JSTUSDT",

    # USDC pairs
    # "BTCUSDC", "ETHUSDC", "ADAUSDC", "DOGEUSDC", "ONDOUSDC",
    # "PEPEUSDC", "XRPUSDC", "WIFUSDC", "BNBUSDC", "SOLUSDC",
    # "TRXUSDC", "ZECUSDC", "ICPUSDC", "PAXGUSDC", "DASHUSDC",
    # "STRKUSDC", "ASTERUSDC", "AAVEUSDC", "ACTUSDC", "ACXUSDC",
    # "AIXBTUSDC", "ALGOUSDC", "API3USDC", "APTUSDC", "ARUSDC",
    # "ARBUSDC", "ARKMUSDC", "ATOMUSDC", "AVAXUSDC", "AXSUSDC",
    # "BANANAUSDC", "BCHUSDC", "TNSRUSDC", "BEAMXUSDC", "BONKUSDC",
    # "CAKEUSDC", "CFXUSDC", "CGPTUSDC", "CHZUSDC", "COOKIEUSDC",
    # "COTIUSDC", "CRVUSDC", "DOTUSDC", "DYDXUSDC", "EGLDUSDC",
    # "ENAUSDC", "ENJUSDC", "ENSUSDC", "ETCUSDC", "FETUSDC",
    # "FILUSDC", "FLOKIUSDC", "FLUXUSDC", "GALAUSDC", "GMTUSDC",
    # "GRTUSDC", "HBARUSDC", "IDEXUSDC", "ILVUSDC", "IMXUSDC",
    # "INJUSDC", "IOUSDC", "JTOUSDC", "JUPUSDC", "KMNOUSDC",
    # "LDOUSDC", "LINKUSDC", "LPTUSDC", "LSKUSDC", "LTCUSDC",
    # "MANTAUSDC", "MASKUSDC", "MINAUSDC", "NEARUSDC", "NEOUSDC",
    # "NMRUSDC", "OMUSDC", "OPUSDC", "ORCAUSDC", "PARTIUSDC",
    # "PENDLEUSDC", "PHAUSDC", "PIXELUSDC", "POLUSDC", "PYTHUSDC",
    # "QNTUSDC", "RAYUSDC", "RENDERUSDC", "ROSEUSDC", "RUNEUSDC",
    # "SUSDC", "SANDUSDC", "SEIUSDC", "SHIBUSDC", "SNXUSDC",
    # "STXUSDC", "SUIUSDC", "SUSHIUSDC", "TAOUSDC", "THEUSDC",
    # "THETAUSDC", "TIAUSDC", "TONUSDC", "TRBUSDC", "TRUMPUSDC",
    # "TURBOUSDC", "UMAUSDC", "UNIUSDC", "UTKUSDC", "VETUSDC",
    # "VIRTUALUSDC", "WUSDC", "WLDUSDC", "XLMUSDC", "XTZUSDC",
    # "YGGUSDC", "ZKUSDC", "ZROUSDC", "WLFIUSDC",
    # "BROCCOLI714USDC", "GUNUSDC", "BERAUSDC", "HUMAUSDC",
    # "BLURUSDC",

    # Crypto pairs
    "ETHBTC",
    "BNBBTC", "BNBETH",
    "ADABTC", "ADAETH", "ADABNB",
    "XRPBTC", "XRPETH", "XRPBNB",
    "TRXBTC", "TRXETH", "TRXBNB",
    "SOLBTC", "SOLETH", "SOLBNB",
    "AVAXBTC", "AVAXETH", "AVAXBNB",
    "ZECBTC", "ZECETH",
    # "APTBTC", "APTETH",
    # "ARBBTC", "ARBETH",
    "ATOMBTC", "ATOMETH",
    # "AXSBTC", "AXSETH",
    # "ARKMBTC", "ARKMBNB",
    # "BANANABTC", "BANANABNB",
    "BCHBTC", "BCHBNB",
    "PAXGBTC",
    "DOGEBTC",
    "ALGOBTC",
    "API3BTC",
    "ARBTC",
    "AUDIOBTC",
    "AXLBTC",
]

ALPHA_TOKENS = [
    "AERO",
    "AIA",
    "AICELL",
    "AIN",
    "AIOT",
    "AITECH",
    "ALCH",
    "ANON",
    "APU",
    "ARTX",
    "AVL",
    "B",
    "BAS",
    "BDXN",
    "BEAT",
    "BIANRENSHENG",
    "BITCOIN",
    "BOB",
    "BTX",
    "BUZZ",
    "COAI",
    "CPOOL",
    "CYS",
    "DRIFT",
    "ESPORTS",
    "FARTCOIN",
    "FHE",
    "FOLKS",
    "GAIA",
    "GAIX",
    "GRIFFAIN",
    "GUA",
    "H",
    "HAJIMI",
    "HANA",
    "ICNT",
    "JELLYJELLY",
    "JCT",
    "KGEN",
    "KO",
    "KOGE",
    "KOMA",
    "LAB",
    "LONG",
    "M",
    "MERL",
    "MEW",
    "MOG",
    "MOODENG",
    "MPLX",
    "NIGHT",
    "LIGHT",
    "OLAS",
    "P",
    "PAAL",
    "PEAQ",
    "PIEVERSE",
    "PIPPIN",
    "PLAY",
    "POPCAT",
    "PORT3",
    "POWER",
    "PTB",
    "QUQ",
    "RAVE",
    "RIVER",
    "RLS",
    "ROAM",
    "RVV",
    "SAFE",
    "SAROS",
    "SENTIS",
    "SHARDS",
    "SHOGGOTH",
    "SKATE",
    "SLX",
    "SPX",
    "STABLE",
    "TAG",
    "TAKE",
    "TCOM",
    "TIMI",
    "TOKEN",
    "TYCOON",
    "UB",
    "US",
    "VELO",
    "VFY",
    "VINU",
    "VRA",
    "VSN",
    "WILD",
    "YALA",
    "ZENT",
    "ZEUS",
    "ZKJ",
]

# -------------------------
# Known quote assets
# -------------------------
KNOWN_QUOTES = (
    "USDT",
    "USDC",
    "BTC",
    "ETH",
    "BNB"
)

# -------------------------
# Excluded symbols from 
# WebSocket price caching
# -------------------------
WS_EXCLUDED_SUFFIXES = (
    "USDC",
    "BTC",
    "ETH",
    "BNB"
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

STABLECOINS = {"USDT", "USDC"}
DEFAULT_QUOTE_ASSET = "USDT"

BINANCE_RATE_LIMIT = "BINANCE_RATE_LIMIT"

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
PORT = os.getenv("PORT", "4747")
DELAY_API_ACCESS_SECONDS = os.getenv("DELAY_API_ACCESS_SECONDS")
SKIP_INITIAL_FETCH = _get_bool_env("SKIP_INITIAL_FETCH", default=False)
ENABLE_WS_PRICE_CACHE = _get_bool_env("ENABLE_WS_PRICE_CACHE", default=False)
ENABLE_FILTER_CACHE = _get_bool_env("ENABLE_FILTER_CACHE", default=False)
GENERATE_FAKE_BALANCE_DATA = _get_bool_env("GENERATE_FAKE_BALANCE_DATA", default=False)

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
if not DELAY_API_ACCESS_SECONDS:
    raise RuntimeError("Missing required environment variable: DELAY_API_ACCESS_SECONDS")


