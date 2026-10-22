"""Shared pytest fixtures: a Flask app wired to in-memory fakes."""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from src.api.app import create_app
from src.auth import rate_limiter

API_KEY = "test-key-123"


class FakeVectorStore:
    def __init__(self) -> None:
        self.indexed: List[Any] = []

    def add_documents(self, documents):
        self.indexed.extend(documents)
        return [f"doc-{i}" for i in range(len(documents))]

    def health_check(self) -> Dict[str, Any]:
        return {"status": "green"}


class FakeRAGService:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def query(self, question: str, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append({"question": question, **kwargs})
        return {
            "answer": f"answer to {question}",
            "sources": [{"document_id": "doc-0", "snippet": "snippet", "score": 0.9, "source": "test.txt"}],
            "metadata": {"top_k": kwargs.get("top_k")},
        }


@pytest.fixture
def env(monkeypatch):
    """Deterministic environment, independent from any local .env file."""
    values = {
        "FLASK_ENV": "testing",
        "AUTH_ENABLED": "true",
        "API_KEYS": API_KEY,
        "RATE_LIMIT_ENABLED": "true",
        "RATE_LIMIT_RPM": "1000",
        "LOG_FILE": "",
        "LOG_LEVEL": "WARNING",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(rate_limiter, "_global_limiter", None)
    return monkeypatch


@pytest.fixture
def fakes():
    return {"vector_store": FakeVectorStore(), "rag_service": FakeRAGService()}


@pytest.fixture
def app(env, fakes):
    app = create_app({"TESTING": True, "LOG_FILE": None})
    app.extensions["vector_store_factory"] = lambda: fakes["vector_store"]
    app.extensions["rag_service_factory"] = lambda: fakes["rag_service"]
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth_headers():
    return {"X-API-Key": API_KEY}
