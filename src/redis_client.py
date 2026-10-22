"""Shared Redis connection (optional: only used when REDIS_URL is set)."""

from __future__ import annotations

import os
from functools import lru_cache


@lru_cache(maxsize=4)
def _client_for(url: str):
    import redis

    return redis.Redis.from_url(url)


def get_redis_client():
    """Return a Redis client when REDIS_URL is configured, else None."""
    url = os.getenv("REDIS_URL")
    return _client_for(url) if url else None
