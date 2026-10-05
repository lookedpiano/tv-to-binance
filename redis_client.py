import redis
import logging
from urllib.parse import urlparse

# ==========================================================
# ========== REDIS SETUP ===================================
# ==========================================================
_r = None

def get_redis() -> redis.Redis:
    """Return the active Redis client or raise if not initialized."""
    if _r is None:
        raise RuntimeError("Redis not initialized. Call init_redis() first.")
    return _r

def init_redis(redis_url: str):
    """Initialize and log Redis connection safely."""
    global _r
    _r = redis.Redis.from_url(redis_url, decode_responses=True)

    parsed = urlparse(redis_url)
    safe_host = parsed.hostname or "unknown"
    safe_db = parsed.path.lstrip("/") or "0"

    # Mask sensitive parts for logging
    masked_host = safe_host.split("-", 1)[0] + "-******" if "-" in safe_host else safe_host
    masked_port = "******" if parsed.port else "unknown"

    logging.info(f"[REDIS] Connected (host={masked_host}:{masked_port}, db={safe_db})")