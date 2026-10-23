"""Authentication package for API security."""

from .identity import Principal, search_scope
from .middleware import current_client_id, current_principal, require_api_key, require_auth, require_role
from .rate_limiter import rate_limit

__all__ = [
    "Principal",
    "current_client_id",
    "current_principal",
    "rate_limit",
    "require_api_key",
    "require_auth",
    "require_role",
    "search_scope",
]
