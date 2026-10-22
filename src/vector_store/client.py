"""Elasticsearch client factory shared by the API and the maintenance scripts."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from elasticsearch import Elasticsearch


def _env_flag(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes"}


def create_es_client(
    url: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> Elasticsearch:
    """Build a client from arguments, falling back to environment variables.

    TLS certificates are verified unless ``ELASTICSEARCH_VERIFY_CERTS=false``;
    ``ELASTICSEARCH_CA_CERTS`` points to a custom CA bundle. Basic auth is only
    sent when a password is configured.
    """
    url = url or os.getenv("ELASTICSEARCH_URL", "http://localhost:9200")
    username = username or os.getenv("ELASTICSEARCH_USERNAME", "elastic")
    password = password or os.getenv("ELASTICSEARCH_PASSWORD") or os.getenv("ELASTIC_PASSWORD")

    options: Dict[str, Any] = {"hosts": [url]}
    if password:
        options["basic_auth"] = (username, password)
    if url.startswith("https"):
        options["verify_certs"] = _env_flag("ELASTICSEARCH_VERIFY_CERTS", True)
        ca_certs = os.getenv("ELASTICSEARCH_CA_CERTS")
        if ca_certs:
            options["ca_certs"] = ca_certs
    return Elasticsearch(**options)
