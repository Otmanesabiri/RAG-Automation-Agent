"""Prometheus metrics instrumentation."""

from __future__ import annotations

import time
from functools import wraps
from typing import Callable

from flask import Flask, request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from .logging import get_logger

logger = get_logger(__name__)


# Metrics definitions
REQUEST_COUNT = Counter(
    "rag_agent_requests_total",
    "Total API requests",
    ["method", "endpoint", "status"],
)

REQUEST_LATENCY = Histogram(
    "rag_agent_request_duration_seconds",
    "Request latency in seconds",
    ["method", "endpoint"],
)

INGESTION_COUNT = Counter(
    "rag_agent_ingestion_documents_total",
    "Total documents ingested",
)

QUERY_COUNT = Counter(
    "rag_agent_queries_total",
    "Total RAG queries executed",
)

EMBEDDING_TOKENS = Counter(
    "rag_agent_embedding_tokens_total",
    "Approximate tokens embedded (characters / 4)",
)


def estimate_tokens(texts) -> int:
    """Cheap provider-independent token estimate (~4 characters per token)."""
    return sum(len(text) for text in texts) // 4


def record_embedding_usage(texts) -> None:
    EMBEDDING_TOKENS.inc(estimate_tokens(texts))


def register_metrics(app: Flask) -> None:
    """Register Prometheus metrics endpoint and request instrumentation."""

    @app.route("/metrics")
    def metrics():
        return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}

    @app.before_request
    def before_request():
        request._start_time = time.time()

    @app.after_request
    def after_request(response):
        if hasattr(request, "_start_time"):
            latency = time.time() - request._start_time
            REQUEST_LATENCY.labels(
                method=request.method,
                endpoint=request.endpoint or "unknown",
            ).observe(latency)

        REQUEST_COUNT.labels(
            method=request.method,
            endpoint=request.endpoint or "unknown",
            status=response.status_code,
        ).inc()

        return response


def track_ingestion(func: Callable) -> Callable:
    """Decorator to track document ingestion metrics."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        result = func(*args, **kwargs)
        # Views return either a Response or a (Response, status) tuple.
        response = result[0] if isinstance(result, tuple) else result
        status = result[1] if isinstance(result, tuple) and len(result) > 1 else getattr(response, "status_code", 200)
        try:
            if isinstance(status, int) and status < 300 and hasattr(response, "get_json"):
                data = response.get_json(silent=True)
                if data and "documents" in data:
                    INGESTION_COUNT.inc(len(data["documents"]))
        except Exception as exc:  # metrics must never break the request
            logger.warning(f"Failed to record ingestion metric: {exc}")
        return result

    return wrapper


def track_query(func: Callable) -> Callable:
    """Decorator to track RAG query metrics."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        QUERY_COUNT.inc()
        return func(*args, **kwargs)

    return wrapper
