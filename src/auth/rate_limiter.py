"""Rate limiting middleware (in-memory, or Redis when REDIS_URL is set)."""

from __future__ import annotations

import os
import time
from collections import defaultdict
from functools import wraps
from typing import Callable, DefaultDict, Dict

from flask import jsonify, request

from ..redis_client import get_redis_client
from .middleware import ANONYMOUS_CLIENT, current_client_id


class RateLimiter:
    """Simple token bucket rate limiter."""

    def __init__(self, requests_per_minute: int = 60) -> None:
        self.requests_per_minute = requests_per_minute
        self.buckets: DefaultDict[str, Dict] = defaultdict(lambda: {"tokens": requests_per_minute, "last_refill": time.time()})

    def is_allowed(self, key: str) -> bool:
        """Check if request is allowed under rate limit."""
        bucket = self.buckets[key]
        now = time.time()
        elapsed = now - bucket["last_refill"]

        # Refill tokens based on elapsed time
        refill_amount = elapsed * (self.requests_per_minute / 60.0)
        bucket["tokens"] = min(self.requests_per_minute, bucket["tokens"] + refill_amount)
        bucket["last_refill"] = now

        if bucket["tokens"] >= 1.0:
            bucket["tokens"] -= 1.0
            return True
        return False


class RedisRateLimiter:
    """Fixed-window (one minute) limiter shared by every worker through Redis."""

    def __init__(self, client, requests_per_minute: int = 60, prefix: str = "rag:ratelimit:") -> None:
        self.client = client
        self.requests_per_minute = requests_per_minute
        self.prefix = prefix

    def is_allowed(self, key: str) -> bool:
        window = int(time.time() // 60)
        redis_key = f"{self.prefix}{key}:{window}"
        pipe = self.client.pipeline()
        pipe.incr(redis_key)
        pipe.expire(redis_key, 120)
        count, _ = pipe.execute()
        return count <= self.requests_per_minute


_global_limiter: RateLimiter | RedisRateLimiter | None = None


def get_rate_limiter() -> RateLimiter | RedisRateLimiter:
    """Get or create the global rate limiter (Redis-backed when REDIS_URL is set)."""
    global _global_limiter
    if _global_limiter is None:
        rpm = int(os.getenv("RATE_LIMIT_RPM", "60"))
        client = get_redis_client()
        if client is None:
            _global_limiter = RateLimiter(requests_per_minute=rpm)
        else:
            _global_limiter = RedisRateLimiter(client, requests_per_minute=rpm)
    return _global_limiter


def rate_limit(func: Callable) -> Callable:
    """Decorator to enforce rate limiting."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        if not _is_rate_limit_enabled():
            return func(*args, **kwargs)

        limiter = get_rate_limiter()
        client_key = _get_client_key()

        if not limiter.is_allowed(client_key):
            return (
                jsonify(
                    {
                        "error": {
                            "code": "rate_limit_exceeded",
                            "message": "Too many requests. Please try again later.",
                        }
                    }
                ),
                429,
            )

        return func(*args, **kwargs)

    return wrapper


def _is_rate_limit_enabled() -> bool:
    """Check if rate limiting is enabled."""
    return os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true"


def _get_client_key() -> str:
    """Generate key for rate limiting based on the authenticated client or IP address.

    Only identities validated by ``require_api_key`` are trusted: keying on the raw
    header would let callers get a fresh bucket by sending a random key.
    """
    client_id = current_client_id()
    if client_id != ANONYMOUS_CLIENT:
        return client_id
    return f"ip:{request.remote_addr}"
