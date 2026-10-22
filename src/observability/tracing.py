"""Optional OpenTelemetry tracing.

Enabled when OTEL_EXPORTER_OTLP_ENDPOINT is set and the OpenTelemetry extras
are installed (requirements-extras.txt). Otherwise every helper is a no-op.
"""

from __future__ import annotations

import os
from contextlib import contextmanager, nullcontext
from typing import Iterator

from .logging import get_logger

logger = get_logger(__name__)

_tracer = None


def configure_tracing(app) -> bool:
    """Export spans over OTLP and instrument Flask; returns True when tracing is active."""
    global _tracer
    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.flask import FlaskInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning("OTEL_EXPORTER_OTLP_ENDPOINT is set but OpenTelemetry extras are not installed")
        return False

    provider = TracerProvider(resource=Resource.create({"service.name": os.getenv("OTEL_SERVICE_NAME", "rag-agent")}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))  # endpoint read from the environment
    trace.set_tracer_provider(provider)
    FlaskInstrumentor().instrument_app(app)
    _tracer = trace.get_tracer("rag-agent")
    logger.info("OpenTelemetry tracing enabled")
    return True


@contextmanager
def span(name: str) -> Iterator[None]:
    context = _tracer.start_as_current_span(name) if _tracer is not None else nullcontext()
    with context:
        yield
