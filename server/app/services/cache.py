import json
import logging
import time
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import settings

logger = logging.getLogger(__name__)

# Redis when REDIS_URL is set and reachable, otherwise a dict with expiry
# times. The dict is per-process, which is fine for a single API container.
_memory: dict[str, tuple[float, str]] = {}
_redis: Redis | None = None
_redis_checked = False


async def _client() -> Redis | None:
    global _redis, _redis_checked
    if _redis_checked:
        return _redis
    _redis_checked = True
    if not settings.redis_url:
        return None
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        await client.ping()
    except RedisError:
        logger.warning(
            "Redis unreachable at %s, using in-memory cache", settings.redis_url
        )
        await client.aclose()
        return None
    _redis = client
    return _redis


async def get_cached(key: str) -> Any | None:
    redis = await _client()
    if redis is not None:
        raw = await redis.get(key)
        return json.loads(raw) if raw else None
    slot = _memory.get(key)
    if slot is None:
        return None
    expires, payload = slot
    if time.time() > expires:
        del _memory[key]
        return None
    return json.loads(payload)


async def put_cached(key: str, value: Any, ttl: int) -> None:
    blob = json.dumps(value)
    redis = await _client()
    if redis is not None:
        await redis.setex(key, ttl, blob)
        return
    _memory[key] = (time.time() + ttl, blob)


async def close() -> None:
    global _redis, _redis_checked
    if _redis is not None:
        await _redis.aclose()
    _redis = None
    _redis_checked = False
