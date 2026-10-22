"""Automations: digests, watches (alerts), structured extraction, signed webhooks."""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Optional

from .digest import build_digest, new_documents
from .extraction import check_schema, extract, validate
from .notify import Notifier
from .watches import WatchStore, run_watches


def verify_webhook_signature(body: bytes, signature: Optional[str], secret: Optional[str] = None) -> bool:
    """Check ``X-Signature: sha256=<hex HMAC of the raw body>`` against WEBHOOK_SECRET."""
    secret = secret if secret is not None else os.getenv("WEBHOOK_SECRET", "")
    if not secret or not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature[len("sha256=") :])


__all__ = [
    "Notifier",
    "WatchStore",
    "build_digest",
    "check_schema",
    "extract",
    "new_documents",
    "run_watches",
    "validate",
    "verify_webhook_signature",
]
