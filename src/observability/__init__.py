"""Observability module for metrics and logging."""

from .logging import configure_logging, get_logger
from .metrics import (
    register_metrics,
    track_ingestion,
    track_query,
    INGESTION_COUNT,
    QUERY_COUNT,
    EMBEDDING_TOKENS,
)

__all__ = [
    "configure_logging",
    "get_logger",
    "register_metrics",
    "track_ingestion",
    "track_query",
    "INGESTION_COUNT",
    "QUERY_COUNT",
    "EMBEDDING_TOKENS",
]
