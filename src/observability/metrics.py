"""Prometheus metrics instrumentation."""

from __future__ import annotations

import time
from functools import wraps
from typing import Callable

from flask import Flask, request
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST


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
    "Total tokens embedded",
)


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
        # Assuming result is a Flask Response with JSON body
        try:
            if hasattr(result, "get_json"):
                data = result.get_json()
                if data and "documents" in data:
                    INGESTION_COUNT.inc(len(data["documents"]))
        except:
            pass
        return result

    return wrapper


def track_query(func: Callable) -> Callable:
    """Decorator to track RAG query metrics."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        QUERY_COUNT.inc()
        return func(*args, **kwargs)

    return wrapper
