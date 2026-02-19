from __future__ import annotations

import logging
from typing import Any, Callable, Coroutine

from cachetools import TTLCache

logger = logging.getLogger(__name__)

_cache: TTLCache = TTLCache(maxsize=256, ttl=600)


def configure_cache(ttl: int) -> None:
    global _cache
    _cache = TTLCache(maxsize=256, ttl=ttl)


def cached(key: str):
    """Decorator that caches async function results by a fixed key."""
    def decorator(func: Callable[..., Coroutine[Any, Any, Any]]):
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            if key in _cache:
                logger.debug("Cache hit: %s", key)
                return _cache[key]
            result = await func(*args, **kwargs)
            _cache[key] = result
            logger.debug("Cache set: %s", key)
            return result
        wrapper.__name__ = func.__name__
        return wrapper
    return decorator


def invalidate_all() -> None:
    _cache.clear()
