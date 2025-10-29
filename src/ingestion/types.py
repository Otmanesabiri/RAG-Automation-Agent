"""Shared typing helpers for ingestion components."""

from __future__ import annotations

from typing import Any, Dict, Protocol


class DocumentLike(Protocol):
    """Structural typing protocol for LangChain ``Document`` objects."""

    page_content: str
    metadata: Dict[str, Any]
