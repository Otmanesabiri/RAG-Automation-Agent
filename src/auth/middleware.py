"""Authentication and authorization middleware."""

from __future__ import annotations

import hashlib
import hmac
import os
from functools import wraps
from typing import Callable, Optional

from flask import Request, g, jsonify, request

ANONYMOUS_CLIENT = "anonymous"


def require_api_key(func: Callable) -> Callable:
    """Decorator to enforce API key authentication.

    On success, ``g.client_id`` holds a stable, non-reversible identifier of the
    caller that downstream code (rate limiting, session ownership) can rely on.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        if not _is_auth_enabled():
            g.client_id = ANONYMOUS_CLIENT
            return func(*args, **kwargs)

        api_key = _extract_api_key(request)
        if not api_key:
            return jsonify({"error": {"code": "missing_api_key", "message": "API key required"}}), 401

        if not _validate_api_key(api_key):
            return jsonify({"error": {"code": "invalid_api_key", "message": "Invalid API key"}}), 403

        g.client_id = client_id_for_key(api_key)
        return func(*args, **kwargs)

    return wrapper


def client_id_for_key(api_key: str) -> str:
    """Derive a client identifier from an API key without storing the key itself."""
    return "key:" + hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


def current_client_id() -> str:
    """Return the authenticated client id, or the anonymous id when auth is off."""
    return getattr(g, "client_id", ANONYMOUS_CLIENT)


def _is_auth_enabled() -> bool:
    """Check if authentication is globally enabled."""
    return os.getenv("AUTH_ENABLED", "false").lower() == "true"


def _extract_api_key(req: Request) -> Optional[str]:
    """Extract API key from X-API-Key header."""
    return req.headers.get("X-API-Key")


def _validate_api_key(api_key: str) -> bool:
    """Validate API key against configured allowed keys using constant-time comparison."""
    allowed_keys = [key.strip() for key in os.getenv("API_KEYS", "").split(",") if key.strip()]
    candidate = api_key.encode("utf-8")
    # Evaluate every key so timing does not reveal which one matched.
    matches = [hmac.compare_digest(candidate, key.encode("utf-8")) for key in allowed_keys]
    return any(matches)
