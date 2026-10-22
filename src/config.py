"""Runtime settings validation."""

from __future__ import annotations

import os
from typing import List

DEV_ENVIRONMENTS = {"development", "testing", "test", "local"}
INSECURE_DEFAULTS = {"", "changeme", "changeme-in-production", "change-this-to-random-secret-key"}


class ConfigurationError(RuntimeError):
    """Raised when the application is started with an unsafe configuration."""


def is_development() -> bool:
    return os.getenv("FLASK_ENV", "production").lower() in DEV_ENVIRONMENTS


def configuration_problems() -> List[str]:
    """Return the list of settings that are unsafe outside development."""
    problems = []
    if os.getenv("SECRET_KEY", "") in INSECURE_DEFAULTS:
        problems.append("SECRET_KEY must be set to a random value")
    if os.getenv("AUTH_ENABLED", "true").lower() == "true":
        if not [k for k in os.getenv("API_KEYS", "").split(",") if k.strip()]:
            problems.append("API_KEYS must contain at least one key when AUTH_ENABLED=true")
    else:
        problems.append("AUTH_ENABLED=false is not allowed in production")
    es_password = os.getenv("ELASTICSEARCH_PASSWORD") or os.getenv("ELASTIC_PASSWORD") or ""
    if es_password in INSECURE_DEFAULTS:
        problems.append("ELASTICSEARCH_PASSWORD must be set to a non-default value")
    return problems


def validate_settings() -> None:
    """Fail fast in production when secrets are missing or left at their defaults."""
    if is_development():
        return
    problems = configuration_problems()
    if problems:
        raise ConfigurationError("Unsafe configuration: " + "; ".join(problems))


def cors_origins() -> List[str]:
    return [origin.strip() for origin in os.getenv("CORS_ORIGINS", "").split(",") if origin.strip()]
