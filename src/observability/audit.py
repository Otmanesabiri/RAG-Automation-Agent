"""Audit trail of security-relevant actions (who did what, in which tenant).

Entries are JSON lines on the ``audit`` logger: in the regular logs, and in
AUDIT_LOG_FILE when it is set.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from loguru import logger

_configured_file = None


def _ensure_file_sink() -> None:
    global _configured_file
    path = os.getenv("AUDIT_LOG_FILE")
    if path and path != _configured_file:
        logger.add(
            path, level="INFO", format="{message}", filter=lambda record: record["extra"].get("audit"), rotation="100 MB"
        )
        _configured_file = path


def audit(action: str, **details: Any) -> None:
    """Record an action performed by the current caller (never raises)."""
    try:
        from flask import has_request_context, request

        from ..auth.middleware import current_principal

        principal = current_principal() if has_request_context() else None
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "client_id": principal.client_id if principal else None,
            "subject": principal.subject if principal else None,
            "tenant": principal.tenant if principal else None,
            "ip": request.remote_addr if has_request_context() else None,
            **details,
        }
        _ensure_file_sink()
        logger.bind(audit=True).info(json.dumps(entry, ensure_ascii=False, default=str))
    except Exception as exc:  # auditing must not break the request
        logger.warning(f"Failed to write audit entry for {action}: {exc}")
