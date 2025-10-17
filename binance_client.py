from binance.spot import Spot as Client
from config._settings import BINANCE_API_KEY, BINANCE_SECRET_KEY

_client = Client(api_key=BINANCE_API_KEY, api_secret=BINANCE_SECRET_KEY)

def get_binance_client():
    """Return the shared Binance Spot client instance."""
    return _client
