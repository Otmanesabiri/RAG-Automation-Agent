"""Authentication and authorization middleware."""

from __future__ import annotations

import os
from functools import wraps
from typing import Callable, Optional

from flask import Request, current_app, jsonify, request


def require_api_key(func: Callable) -> Callable:
    """Decorator to enforce API key authentication."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        if not _is_auth_enabled():
            return func(*args, **kwargs)

        api_key = _extract_api_key(request)
        if not api_key:
            return jsonify({"error": {"code": "missing_api_key", "message": "API key required"}}), 401

        if not _validate_api_key(api_key):
            return jsonify({"error": {"code": "invalid_api_key", "message": "Invalid API key"}}), 403

        return func(*args, **kwargs)

    return wrapper


def _is_auth_enabled() -> bool:
    """Check if authentication is globally enabled."""
    return os.getenv("AUTH_ENABLED", "false").lower() == "true"


def _extract_api_key(req: Request) -> Optional[str]:
    """Extract API key from X-API-Key header."""
    return req.headers.get("X-API-Key")


def _validate_api_key(api_key: str) -> bool:
    """Validate API key against configured allowed keys."""
    allowed_keys = os.getenv("API_KEYS", "").split(",")
    allowed_keys = [key.strip() for key in allowed_keys if key.strip()]
    if not allowed_keys:
        # If no keys configured but auth is enabled, default to a single demo key
        allowed_keys = [os.getenv("API_KEY_DEMO", "demo-key-change-me")]
    return api_key in allowed_keys
