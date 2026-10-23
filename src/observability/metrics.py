"""Prometheus metrics instrumentation."""

from __future__ import annotations

import json
import os
import re
import time
from contextlib import contextmanager
from functools import wraps
from typing import Callable, Dict, Iterator, Optional

from flask import Flask, request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from . import tracing
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


CITATION_CONFIDENCE = Histogram(
    "rag_agent_citation_confidence",
    "Share of answer claims supported by the sources (citation check)",
    ["mode"],
    buckets=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
)

UNGROUNDED_ANSWERS = Counter(
    "rag_agent_ungrounded_answers_total",
    "Answers whose citation check found them insufficiently grounded",
    ["mode"],
)


def record_citation_check(confidence: float, is_grounded: bool, mode: str) -> None:
    CITATION_CONFIDENCE.labels(mode=mode).observe(confidence)
    if not is_grounded:
        UNGROUNDED_ANSWERS.labels(mode=mode).inc()


LLM_TOKENS = Counter(
    "rag_agent_llm_tokens_total",
    "LLM tokens reported by the provider",
    ["provider", "model", "kind"],  # kind: input | output
)

LLM_COST = Counter(
    "rag_agent_llm_cost_usd_total",
    "Estimated LLM spend in USD (models with a known price only)",
    ["provider", "model"],
)

STAGE_LATENCY = Histogram(
    "rag_agent_stage_duration_seconds",
    "Latency of each RAG pipeline stage",
    ["stage"],  # query_rewrite | retrieval | rerank | generation | citation_check | embedding
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)

ANSWERS = Counter(
    "rag_agent_answers_total",
    "Generated answers, by whether the model said the documents lacked the information",
    ["outcome"],  # answered | no_information
)

SOURCES_PER_ANSWER = Histogram(
    "rag_agent_sources_per_answer",
    "Number of chunks given to the LLM per answer",
    buckets=(0, 1, 2, 3, 4, 5, 6, 8, 10, 15, 20),
)

# USD per million tokens (input, output). Extend or override with LLM_PRICES,
# a JSON object such as {"my-model": [1.0, 5.0]}.
DEFAULT_PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}

_NO_INFORMATION_PATTERNS = re.compile(
    r"(i don't know|i do not know|(context|sources?|documents?) (does|do) not (contain|provide|mention)"
    r"|not enough information|no relevant (context|information)|je ne sais pas|ne (contient|contiennent|mentionne) pas"
    r"|aucune information|pas d'information)",
    re.IGNORECASE,
)


def _prices() -> Dict[str, tuple]:
    prices = dict(DEFAULT_PRICES)
    raw = os.getenv("LLM_PRICES")
    if raw:
        try:
            prices.update({model: tuple(value) for model, value in json.loads(raw).items()})
        except (ValueError, TypeError, AttributeError) as exc:
            logger.warning(f"Ignoring invalid LLM_PRICES: {exc}")
    return prices


def record_llm_usage(provider: str, model: str, input_tokens: Optional[int], output_tokens: Optional[int]) -> None:
    """Count tokens and estimated cost of one LLM call; never raises."""
    try:
        input_tokens, output_tokens = int(input_tokens or 0), int(output_tokens or 0)
        LLM_TOKENS.labels(provider=provider, model=model, kind="input").inc(input_tokens)
        LLM_TOKENS.labels(provider=provider, model=model, kind="output").inc(output_tokens)
        price = _prices().get(model)
        if price:
            LLM_COST.labels(provider=provider, model=model).inc(
                (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000
            )
    except Exception as exc:
        logger.warning(f"Failed to record LLM usage: {exc}")


def is_no_information_answer(answer: str) -> bool:
    return bool(_NO_INFORMATION_PATTERNS.search(answer or ""))


def record_answer(answer: str, source_count: int) -> None:
    ANSWERS.labels(outcome="no_information" if is_no_information_answer(answer) else "answered").inc()
    SOURCES_PER_ANSWER.observe(source_count)


@contextmanager
def stage_timer(stage: str) -> Iterator[None]:
    """Time a pipeline stage (Prometheus histogram) and trace it (OpenTelemetry span, if configured)."""
    start = time.perf_counter()
    with tracing.span(f"rag.{stage}"):
        try:
            yield
        finally:
            STAGE_LATENCY.labels(stage=stage).observe(time.perf_counter() - start)


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
