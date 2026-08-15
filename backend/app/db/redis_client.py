import logging
import redis
from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

def get_redis_client():
    """
    Creates and returns a Redis client connection.
    Gracefully returns None if Redis is unreachable so the app doesn't crash.
    """
    try:
        client = redis.from_url(
            settings.redis_url, 
            decode_responses=True,
            socket_connect_timeout=2
        )
        client.ping()
        logger.info("Connected to Redis successfully.")
        return client
    except Exception as e:
        logger.warning(f"Redis connection failed: {e}. Falling back to non-cached execution.")
        return None

# Global Redis client instance
redis_db = get_redis_client()