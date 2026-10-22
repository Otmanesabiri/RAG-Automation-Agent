"""Bulk indexing, idempotent re-ingestion and document management."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.vector_store import elasticsearch_store
from src.vector_store.elasticsearch_store import ElasticsearchVectorStore


class _Embeddings:
    def embed_documents(self, texts):
        return [[float(len(t))] for t in texts]


def _chunk(source, index, content):
    return SimpleNamespace(page_content=content, metadata={"source": source, "chunk_index": index})


@pytest.fixture
def bulk_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(elasticsearch_store.helpers, "bulk", lambda client, actions, **kw: calls.append((list(actions), kw)))
    return calls


@pytest.fixture
def store():
    return ElasticsearchVectorStore("idx", _Embeddings(), client=MagicMock(), refresh="wait_for")


def test_bulk_indexes_all_chunks_in_one_call(store, bulk_calls):
    ids = store.add_documents([_chunk("a.txt", 0, "one"), _chunk("a.txt", 1, "two")])

    assert len(bulk_calls) == 1
    actions, kwargs = bulk_calls[0]
    assert [a["_id"] for a in actions] == ids
    assert kwargs["refresh"] == "wait_for"
    doc_id = ElasticsearchVectorStore.document_id_for("a.txt")
    assert {a["_source"]["document_id"] for a in actions} == {doc_id}
    store.client.index.assert_not_called()


def test_reingesting_same_content_yields_same_ids(store, bulk_calls):
    first = store.add_documents([_chunk("a.txt", 0, "one")])
    second = store.add_documents([_chunk("a.txt", 0, "one")])
    assert first == second


def test_new_version_deletes_stale_chunks(store, bulk_calls):
    ids = store.add_documents([_chunk("a.txt", 0, "new content")])

    query = store.client.delete_by_query.call_args.kwargs["query"]
    assert query["bool"]["filter"] == [{"term": {"document_id": ElasticsearchVectorStore.document_id_for("a.txt")}}]
    assert query["bool"]["must_not"] == [{"ids": {"values": ids}}]


def test_get_document_orders_chunks(store):
    store.client.search.return_value = {
        "hits": {
            "hits": [
                {"_source": {"id": "c2", "content": "second", "source": "a.txt", "metadata": {"chunk_index": 1}}},
                {"_source": {"id": "c1", "content": "first", "source": "a.txt", "metadata": {"chunk_index": 0}}},
            ]
        }
    }
    document = store.get_document("doc")
    assert [c["content"] for c in document["chunks"]] == ["first", "second"]


def test_get_unknown_document_returns_none(store):
    store.client.search.return_value = {"hits": {"hits": []}}
    assert store.get_document("missing") is None


def test_list_documents_paginates(store):
    store.client.search.return_value = {
        "aggregations": {
            "documents": {
                "after_key": {"document_id": "d1"},
                "buckets": [
                    {
                        "key": {"document_id": "d1"},
                        "doc_count": 3,
                        "sample": {"hits": {"hits": [{"_source": {"source": "a.txt", "metadata": {"mime_type": "text/plain"}}}]}},
                        "last_indexed": {"value_as_string": "2026-01-01T00:00:00Z"},
                    }
                ],
            }
        }
    }
    page = store.list_documents(size=1)
    assert page["documents"][0] == {
        "document_id": "d1",
        "source": "a.txt",
        "mime_type": "text/plain",
        "chunks": 3,
        "indexed_at": "2026-01-01T00:00:00Z",
    }
    assert page["next"] == "d1"


def test_document_endpoints(client, auth_headers):
    payload = {"source": "upload", "documents": [{"content": "hello world", "filename": "notes.txt"}]}
    ingest = client.post("/ingest", json=payload, headers=auth_headers).get_json()
    document_id = ingest["documents"][0]["document_id"]
    assert ingest["documents"][0]["source"] == "notes.txt"

    listed = client.get("/documents", headers=auth_headers).get_json()["documents"]
    assert [d["document_id"] for d in listed] == [document_id]
    assert client.get(f"/documents/{document_id}", headers=auth_headers).status_code == 200

    deleted = client.delete(f"/documents/{document_id}", headers=auth_headers)
    assert deleted.get_json()["deleted_chunks"] == 1
    assert client.get(f"/documents/{document_id}", headers=auth_headers).status_code == 404
    assert client.delete(f"/documents/{document_id}", headers=auth_headers).status_code == 404


def test_document_endpoints_require_auth(client):
    assert client.get("/documents").status_code == 401
    assert client.delete("/documents/x").status_code == 401
