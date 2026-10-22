"""End-to-end vector store tests against a real Elasticsearch.

Run with ``pytest -m integration``; skipped when ELASTICSEARCH_URL is unreachable.
"""

import hashlib
import os
import uuid
from types import SimpleNamespace

import pytest

from src.vector_store.client import create_es_client
from src.vector_store.elasticsearch_store import ElasticsearchVectorStore

pytestmark = pytest.mark.integration

DIMS = 16


class HashEmbeddings:
    """Deterministic bag-of-words embeddings: shared words => similar vectors."""

    dimension = DIMS
    model_name = "hash-test"

    def _embed(self, text):
        vector = [0.0] * DIMS
        for word in text.lower().split():
            vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % DIMS] += 1.0
        return vector if any(vector) else [1.0] + [0.0] * (DIMS - 1)

    def embed_documents(self, texts):
        return [self._embed(t) for t in texts]

    def embed_query(self, text):
        return self._embed(text)


@pytest.fixture
def store():
    client = create_es_client()
    try:
        if not client.options(request_timeout=2, max_retries=0).ping():
            pytest.skip("Elasticsearch is not reachable")
    except Exception:
        pytest.skip("Elasticsearch is not reachable")
    index = f"test-rag-{uuid.uuid4().hex[:8]}"
    store = ElasticsearchVectorStore(index, HashEmbeddings(), client=client, refresh="true")
    store.ensure_index()
    yield store
    client.indices.delete(index=index, ignore_unavailable=True)


def _chunks(source, texts, **metadata):
    return [
        SimpleNamespace(page_content=t, metadata={"source": source, "chunk_index": i, **metadata})
        for i, t in enumerate(texts)
    ]


def test_index_search_and_document_lifecycle(store):
    store.add_documents(_chunks("geo.txt", ["paris is the capital of france", "berlin is the capital of germany"], category="geo"))
    store.add_documents(_chunks("ml.txt", ["machine learning trains models on data"], category="ml"))

    results = store.similarity_search("capital of france", k=1)
    assert results[0]["source"] == "geo.txt"
    assert results[0]["document_id"] == ElasticsearchVectorStore.document_id_for("geo.txt")

    filtered = store.similarity_search("capital", k=5, filters={"category": "ml"})
    assert {r["source"] for r in filtered} == {"ml.txt"}

    listed = store.list_documents()
    assert {d["source"]: d["chunks"] for d in listed["documents"]} == {"geo.txt": 2, "ml.txt": 1}

    # Re-ingesting a shorter version replaces the old chunks instead of duplicating them.
    store.add_documents(_chunks("geo.txt", ["paris is the capital of france"], category="geo"))
    geo_id = ElasticsearchVectorStore.document_id_for("geo.txt")
    assert len(store.get_document(geo_id)["chunks"]) == 1

    assert store.delete_document(geo_id) == 1
    assert store.get_document(geo_id) is None


def test_ensure_index_is_idempotent(store):
    assert store.ensure_index() is False


def test_hybrid_search_finds_exact_terms(store):
    store.add_documents(_chunks("codes.txt", ["error code XK-4471 means the disk is full", "general maintenance notes"]))
    store.add_documents(_chunks("other.txt", ["the printer is out of paper"]))

    for mode in ("sparse", "hybrid"):
        results = store.similarity_search("XK-4471", k=1, mode=mode)
        assert results[0]["content"].startswith("error code XK-4471"), mode
