"""Caller identity: tenant, document access levels and roles.

Two ways to authenticate:
- API key (``X-API-Key``): a service identity. By default it has the ``admin``
  role in the ``default`` tenant with access to every document; API_KEY_SCOPES
  can restrict individual keys.
- JWT (``Authorization: Bearer``), e.g. from an OIDC provider: tenant, access
  levels and roles come from token claims, never from the request payload.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

ROLES = ("reader", "editor", "admin")  # each role includes the ones before it
DEFAULT_TENANT = "default"


class AuthenticationError(Exception):
    """Invalid or expired credentials."""


@dataclass(frozen=True)
class Principal:
    client_id: str
    tenant: str = DEFAULT_TENANT
    roles: Tuple[str, ...] = ("admin",)
    # None = every document; otherwise documents without access_level or with one of these
    access_levels: Optional[Tuple[str, ...]] = None
    subject: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    def has_role(self, role: str) -> bool:
        wanted = ROLES.index(role)
        return any(r in ROLES and ROLES.index(r) >= wanted for r in self.roles)


def multi_tenant_enabled() -> bool:
    return os.getenv("MULTI_TENANT", "false").lower() == "true"


def principal_for_api_key(client_id: str, api_key: str) -> Principal:
    scopes = _api_key_scopes().get(hashlib.sha256(api_key.encode("utf-8")).hexdigest()) or _api_key_scopes().get(api_key)
    if not scopes:
        return Principal(client_id=client_id)
    levels = scopes.get("access_levels")
    return Principal(
        client_id=client_id,
        tenant=scopes.get("tenant", DEFAULT_TENANT),
        roles=tuple(scopes.get("roles", ["reader"])),
        access_levels=tuple(levels) if levels is not None else None,
    )


def _api_key_scopes() -> Dict[str, Dict[str, Any]]:
    """API_KEY_SCOPES: JSON keyed by API key or its SHA-256 hex digest (preferred)."""
    raw = os.getenv("API_KEY_SCOPES")
    return json.loads(raw) if raw else {}


def jwt_enabled() -> bool:
    return bool(os.getenv("JWT_JWKS_URL") or os.getenv("JWT_SECRET"))


@lru_cache(maxsize=4)
def _jwks_client(url: str):
    import jwt

    return jwt.PyJWKClient(url, cache_keys=True)


def principal_for_jwt(token: str) -> Principal:
    """Validate signature, expiry, audience and issuer, then map claims to a principal."""
    import jwt

    options: Dict[str, Any] = {"require": ["exp", "sub"]}
    audience = os.getenv("JWT_AUDIENCE") or None
    issuer = os.getenv("JWT_ISSUER") or None
    try:
        if os.getenv("JWT_JWKS_URL"):
            key = _jwks_client(os.environ["JWT_JWKS_URL"]).get_signing_key_from_jwt(token).key
            algorithms = os.getenv("JWT_ALGORITHMS", "RS256,ES256").split(",")
        else:
            key = os.environ["JWT_SECRET"]
            algorithms = ["HS256"]
        claims = jwt.decode(token, key, algorithms=algorithms, audience=audience, issuer=issuer, options=options)
    except (jwt.PyJWTError, KeyError) as exc:
        raise AuthenticationError(str(exc)) from exc

    roles = _claim_list(claims, os.getenv("JWT_ROLES_CLAIM", "roles")) or ["reader"]
    levels = _claim_list(claims, os.getenv("JWT_ACCESS_CLAIM", "access_levels"))
    tenant = claims.get(os.getenv("JWT_TENANT_CLAIM", "tenant"))
    if multi_tenant_enabled() and not tenant:
        raise AuthenticationError("Token has no tenant claim")
    return Principal(
        client_id="jwt:" + hashlib.sha256(f"{claims.get('iss')}|{claims['sub']}".encode()).hexdigest()[:16],
        tenant=str(tenant or DEFAULT_TENANT),
        roles=tuple(roles),
        # Users are restricted to the levels in their token (none = public documents only).
        access_levels=tuple(levels or ()),
        subject=str(claims["sub"]),
    )


def _claim_list(claims: Dict[str, Any], path: str) -> List[str]:
    """Read a claim that may be nested (e.g. 'realm_access.roles') and a list or a space-separated string."""
    value: Any = claims
    for part in path.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    if value is None:
        return []
    if isinstance(value, str):
        return value.split()
    return [str(v) for v in value]


def search_scope(principal: Optional[Principal]) -> Tuple[Dict[str, Any], Optional[List[str]]]:
    """Mandatory search restrictions for a caller: (metadata filters, access levels)."""
    if principal is None:
        return {}, None
    filters = {"tenant": principal.tenant} if multi_tenant_enabled() else {}
    levels = list(principal.access_levels) if principal.access_levels is not None else None
    return filters, levels
