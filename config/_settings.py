import os

from zoneinfo import ZoneInfo


CMC_ASSET_IDS = {
    "BTC": 1,
    "LTC": 2,
    "ETH": 1027,
    "BNB": 1839,
    "XRP": 52,
    "ADA": 2010,
    "DOGE": 74,
    "SOL": 5426,
    "LUNC": 4172,
    "ICP": 8916,
    "MEGA": 38770,
    "RARE": 11294,
    "NIGHT": 39064,
    "MEW": 30126,
    "MANA": 1966,
    "KERNEL": 36180,
    "IMX": 10603,
    "FOLKS": 38864,
    "BCH": 1831,
    "ARX": 39970,
    "MOVE": 32452,
    "PTB": 37372,
    "JST": 5488,
    "HUMA": 36576,
    "W": 29587,
    "TIA": 22861,
    "RSR": 3964,
    "HBAR": 4642,
    "FET": 3773,
    "0G": 38337,
    "PUMP": 36507,
    "PUMPFUN": 36507,
    "ALICE": 8766,
    "SOON": 36542,
    "SFP": 8119,
    "ROSE": 7653,
    "PROS": 39682,
    "GRASS": 32956,
    "AVA": 2776,
    "ALGO": 4030,
    "SEI": 23149,
    "OP": 11840,
    "EDEN": 38513,
    "BEAM": 28298,
    "NOT": 28850,
    "METIS": 9640,
    "SUI": 20947,
    "STRK": 22691,
    "ONDO": 21159,
    "KNC": 9444,
    "GAS": 1785,
    "FIL": 2280,
    "CHR": 3978,
    "ASTR": 12885,
    "AAVE": 7278,
    "ALPINE": 18112,
    "KITE": 38828,
    "SIGN": 35600,
    "PUNDIX": 9040,
    "MORPHO": 34104,
    "GRT": 6719,
    "EDGE": 39720,
    "DOT": 6636,
    "COMP": 5692,
    "ARIA": 38102,
    "XPL": 36645,
    "TAO": 22974,
    "SENT": 38868,
    "S": 32684,
    "KSM": 5034,
    "GOAT": 33440,
    "CETUS": 25114,
    "SAGA": 30372,
    "ZRO": 26997,
    "XTZ": 2011,
    "PLUME": 35364,
    "BSV": 3602,
    "CVC": 1816,
    "A": 36462,
    "FLOCK": 34987,
    "MAV": 18037,
    "BABY": 32198,
    "TRB": 4944,
    "AVAX": 5805,
    "CRO": 3635,
    "WIF": 28752,
    "ARB": 11841,
    "RLC": 1637,
    "CFX": 7334,
    "MAGIC": 14783,
    "NIL": 35702,
    "MTL": 1788,
    "KAS": 20396,
    "CELR": 3814,
    "ANKR": 3783,
    "STX": 4847,
    "MERL": 30712,
    "USUAL": 33979,
    "ZAMA": 39332,
    "SOMI": 37637,
    "JUP": 29210,
    "ENS": 13855,
    "ONE": 3945,
    "HAEDAL": 36369,
    "AVNT": 38299,
    "MANTA": 13631,
    "YGG": 10688,
    "IOTX": 2777,
    "APT": 21794,
    "NEO": 1376,
    "STEEM": 1230,
    "POWR": 2132,
    "MNT": 27075,
    "INJ": 7226,
    "ORDI": 25028,
    "DOGS": 32698,
    "ANIME": 35319,
    "QTUM": 1684,
    "API3": 7737,
    "VTHO": 3012,
    "THETA": 2416,
    "KAVA": 4846,
    "LSK": 1214,
    "FORM": 23635,
    "REZ": 30843,
    "LDO": 8000,
    "AR": 5632,
    "BLUR": 23121,
    "MON": 30495,
    "C": 37340,
    "JOE": 11396,
    "LISTA": 21533,
    "TST": 35647,
    "ARK": 1586,
    "ORDER": 32809,
    "AZTEC": 39521,
    "ALT": 29073,
    "ZRX": 1896,
    "ENJ": 2130,
    "CATI": 32966,
    "SAHARA": 36671,
    "BROCCOLI": 35749,
    "BROCCOLI714": 35749,
    "ZETA": 21259,
    "VET": 3077,
    "INIT": 33120,
    "MINA": 8646,
    "SSV": 12999,
    "ZORA": 35931,
    "CARV": 33372,
    "UNI": 7083,
    "EDU": 24613,
    "WAXP": 2300,
    "PORTAL": 29555,
    "KMNO": 30986,
    "FARTCOIN": 33597,
    "VIRTUAL": 29420,
    "SUPER": 8290,
    "EVAA": 38376,
    "POL": 28321,
    "PEPE": 24478,
    "SATS": 28194,
    "PYTH": 28177,
    "CORE": 23254,
    "BabyDoge": 10407,
    "1MBABYDOGE": 10407,
    "CKB": 4948,
    "XAI": 28933,
    "BAND": 4679,
    "GTC": 10052,
    "HYPE": 32196,
    "SWARMS": 34993,
    "T": 17751,
    "CELO": 5567,
    "CAT": 32724,
    "STBL": 38359,
    "WET": 39049,
    "Q": 38236,
    "JELLYJELLY": 35537,
    "RECALL": 38669,
    "FHE": 36153,
    "AIA": 38430,
    "BMT": 35214,
    "CGPT": 23756,
    "XAN": 38481,
    "TURTLE": 38671,
    "CTSI": 5444,
    "SKYAI": 36300,
    "RUNE": 4157,
    "ENA": 30171,
    "GIGGLE": 38470,
    "ENSO": 38668,
    "ORCA": 11165,
    "COAI": 38489,
    "OKB": 3897,
    "RPL": 2943,
    "ACT": 33566,
    "EIGEN": 30494,
    "SPCX": 40238,
    "TSLA": 40691,
    "PIPPIN": 35053,
    "1INCH": 8104,
    "PENDLE": 9481,
    "APE": 18876,
    "CHILLGUY": 34125,
    "ZK": 24091,
    "SAPIEN": 38117,
    "PENGU": 34466,
    "SNX": 2586,
    "LINEA": 27657,
    "GRIFFAIN": 34792,
    "PAXG": 4705,
    "AEVO": 29676,
    "4": 38557,
    "AWE": 4006,
    "BANANA": 28066,
    "BTR": 36277,
    "CHEEMS": 33280,
    "COTI": 3992,
    "ETHW": 21296,
    "G": 32120,
    "HOLO": 38309,
    "IOTA": 1720,
    "KAT": 38769,
    "ONG": 3217,
    "PNUT": 33788,
    "QNT": 33788,
    "SLP": 5824,
    "SUN": 10529,
    "TAC": 37338,
    "TRX": 1958,
    "ACH": 6958,
    "ETC": 1321,
    "FLUID": 10508,
    "NOM": 38464,
    "BOZO": 29308,
    "BENK": 30756,
    "PUNCH": 39600,
    "SNEK": 25264,
    "ARPA": 4039,
    "DIA": 6138,
    "MOVR": 9285,
    "NMR": 1732,
    "RENDER": 5690,
    "LUMIA": 33439,
    "AI": 39883,
}

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
    "BLURUSDT", "SXTUSDT", "METUSDT", "XAIUSDT", "KAIAUSDT",
    "ENSOUSDT", "NOMUSDT", "RESOLVUSDT",
    
    # no USDC pairs
    "XNOUSDT", "BATUSDT", "DUSKUSDT", "GLMUSDT", "AUDIOUSDT",
    "AXLUSDT", "BICOUSDT", "BNSOLUSDT", "BTTCUSDT", "C98USDT",
    "CTKUSDT", "DATAUSDT", "DODOUSDT", "FIDAUSDT", "FLOWUSDT",
    "FXSUSDT", "GLMRUSDT", "HFTUSDT", "HOOKUSDT", "IQUSDT",
    "JASMYUSDT", "JOEUSDT", "KNCUSDT", "LUNAUSDT", "MANAUSDT",
    "MOVRUSDT", "NEXOUSDT", "NTRNUSDT", "POLYXUSDT", "PONDUSDT",
    "PYRUSDT", "RLCUSDT", "RONINUSDT", "SCRUSDT", "SPELLUSDT",
    "SUPERUSDT", "TFUELUSDT", "VTHOUSDT", "WOOUSDT", "XECUSDT",
    "JSTUSDT", "METISUSDT", "ARPAUSDT", "REZUSDT", "ROBOUSDT",

    # Bitunix
    "BASEDUSDT", "AKEUSDT", "HIGHUSDT", "CETUSUSDT", "SPKUSDT",
    "LINEAUSDT", "AGLDUSDT", "ZBTUSDT", "IRYSUSDT", "SWARMSUSDT",
    "RIFUSDT", "HYPERUSDT", "EPICUSDT", "ALICEUSDT", "HUSDT",
    "ZETAUSDT", "PENGUUSDT", "KAITOUSDT", "BOMEUSDT", "SAPIENUSDT",
    "BABYUSDT", "BIOUSDT", "AVAUSDT", "CHILLGUYUSDT", "CROSSUSDT",
    "ZENUSDT", "ASTRUSDT", "APEUSDT", "BUSDT", "TSTUSDT", "MTLUSDT",
    "AIOUSDT", "INITUSDT", "SLPUSDT", "SAHARAUSDT", "1INCHUSDT",
    "TURTLEUSDT", "GTCUSDT", "VICUSDT", "CCUSDT", "GPSUSDT",
    "SPCXUSDT", "BANKUSDT", "SQDUSDT", "HEIUSDT", "PROMUSDT",
    "SYNUSDT", "EIGENUSDT", "COMPUSDT", "NXPCUSDT", "PNUTUSDT",
    "GRASSUSDT", "MEMEUSDT", "XANUSDT", "TSLAUSDT", "TAIKOUSDT",
    "EVAAUSDT", "VANRYUSDT", "IDUSDT", "XPINUSDT", "TWTUSDT",
    "TUTUSDT", "OGUSDT", "ATUSDT", "ALLOUSDT", "WALUSDT", "PUMPFUNUSDT",
    "ALTUSDT", "GMXUSDT", "RPLUSDT", "SSVUSDT", "USELESSUSDT",
    "XNYUSDT", "FLUIDUSDT", "XVSUSDT", "BSVUSDT", "APRUSDT", "KASUSDT",
    "GOATUSDT", "OKBUSDT", "SOONUSDT", "PEOPLEUSDT", "HOLOUSDT", "AEONUSDT",
    "ETHWUSDT", "SATSUSDT", "GIGGLEUSDT", "COLLECTUSDT", "GUSDT", "CVXUSDT",
    "SKYAIUSDT", "CTSIUSDT", "HYPEUSDT", "AEVOUSDT", "BOZOUSDT", "SNEKUSDT", 
    "PUNCHUSDT", "FORMUSDT", "ACEUSDT", "CHEEMSUSDT", "RAREUSDT", "MUBARAKUSDT",
    "BANDUSDT", "BMTUSDT", "4USDT", "OPENUSDT", "PLUMEUSDT", "EDENUSDT", "NOTUSDT",
    "SUNUSDT", "RECALLUSDT", "QUSDT", "ONGUSDT", "WETUSDT", "HEMIUSDT", "ETHFIUSDT",
    "STBLUSDT", "VVVUSDT", "CATUSDT", "CELOUSDT", "TUSDT", "IOTAUSDT", "KSMUSDT",
    "BABYDOGEUSDT", "1MBABYDOGEUSDT", "COREUSDT", "CYBERUSDT", "CKBUSDT", "TACUSDT",
    "NILUSDT", "PORTALUSDT", "WAXPUSDT", "EDUUSDT", "CARVUSDT", "ZORAUSDT",
    "IOTXUSDT", "CATIUSDT", "ZRXUSDT", "AZTECUSDT", "ORDERUSDT", "ARKUSDT",
    "LISTAUSDT", "FFUSDT", "KATUSDT", "KAVAUSDT", "QTUMUSDT", "ANIMEUSDT",
    "DOGSUSDT", "ORDIUSDT", "MNTUSDT", "POWRUSDT", "STEEMUSDT", "AVNTUSDT",
    "HAEDALUSDT", "ONEUSDT", "SOMIUSDT", "USUALUSDT", "ANKRUSDT", "CELRUSDT",
    "MAGICUSDT", "CROUSDT", "MAVUSDT", "FLOCKUSDT", "AUSDT", "CVCUSDT",
    "SAGAUSDT", "SENTUSDT", "XPLUSDT", "ARIAUSDT", "EDGEUSDT", "MORPHOUSDT",
    "PUNDIXUSDT", "SIGNUSDT", "KITEUSDT", "ALPINEUSDT", "AWEUSDT", "CHRUSDT",
    "GASUSDT", "BEAMUSDT", "PROSUSDT", "SFPUSDT", "0GUSDT", "RSRUSDT",
    "MOVEUSDT", "ARXUSDT", "KERNELUSDT", "MEGAUSDT", "CUSDT", "MONUSDT",
    "ACHUSDT", "DIAUSDT", "LUMIAUSDT", "AIGENSYNUSDT",
    


    # ARCUSDT available on Bitunix spot market

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
    # "BLURUSDC", "SXTUSDC", "METUSDC", "XAIUSDC", "KAIAUSDC",
    # "ENSOUSDC", "NOMUSDC", "RESOLVUSDC",

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
    "ACU",
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
    "BR",
    "BTR",
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
    "IN",
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
    "PROMPT",
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
    "SIREN",
    "SKATE",
    "SKR",
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
# TIMEZONE CONFIG
# -------------------------
TZ = ZoneInfo("Europe/Zurich")

# -------------------------
# 12h ASSET PRICE SNAPSHOTS Binance
# -------------------------
ASSET_PRICE_SNAPSHOT_PREFIX = "asset_price_snapshot"
ASSET_PRICE_SNAPSHOT_CHECK_INTERVAL = 60 * 30  # every 30 minutes

# Keep this many days of completed 12-hour snapshots.
# Change this value whenever you want to retain more/less history.
ASSET_PRICE_SNAPSHOT_RETENTION_DAYS = 10

# -------------------------
# 12h ASSET PRICE SNAPSHOTS CMC
# -------------------------
CMC_API_KEY = os.getenv("CMC_API_KEY")

CMC_API_BASE_URL = "https://pro-api.coinmarketcap.com"

CMC_PRICE_SNAPSHOT_PREFIX = "asset_price_snapshot_cmc"
CMC_PRICE_SNAPSHOT_CHECK_INTERVAL = 60 * 30
CMC_PRICE_SNAPSHOT_RETENTION_DAYS = 10

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


