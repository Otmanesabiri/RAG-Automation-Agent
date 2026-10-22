"""Shared pytest fixtures: a Flask app wired to in-memory fakes."""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from src.api.app import create_app
from src.auth import rate_limiter
from src.vector_store.elasticsearch_store import ElasticsearchVectorStore

API_KEY = "test-key-123"


class FakeVectorStore:
    document_id_for = staticmethod(ElasticsearchVectorStore.document_id_for)

    def __init__(self) -> None:
        self.indexed: List[Any] = []

    def add_documents(self, documents):
        self.indexed.extend(documents)
        return [f"doc-{i}" for i in range(len(documents))]

    def _by_document(self) -> Dict[str, List[Any]]:
        grouped: Dict[str, List[Any]] = {}
        for doc in self.indexed:
            grouped.setdefault(self.document_id_for(doc.metadata["source"]), []).append(doc)
        return grouped

    def list_documents(self, *, size=20, after=None):
        docs = [
            {"document_id": doc_id, "source": chunks[0].metadata["source"], "chunks": len(chunks)}
            for doc_id, chunks in sorted(self._by_document().items())
        ]
        return {"documents": docs[:size], "next": None}

    def get_document(self, document_id):
        chunks = self._by_document().get(document_id)
        if not chunks:
            return None
        return {"document_id": document_id, "source": chunks[0].metadata["source"], "chunks": len(chunks)}

    def delete_document(self, document_id):
        chunks = self._by_document().get(document_id, [])
        self.indexed = [doc for doc in self.indexed if doc not in chunks]
        return len(chunks)

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
        "REDIS_URL": "",
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
