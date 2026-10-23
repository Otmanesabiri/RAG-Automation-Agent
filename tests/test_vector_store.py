"""Bulk indexing, idempotent re-ingestion and document management."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.vector_store import elasticsearch_store
from src.vector_store.elasticsearch_store import ElasticsearchVectorStore, IndexDimensionMismatchError


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
                        "sample": {
                            "hits": {"hits": [{"_source": {"source": "a.txt", "metadata": {"mime_type": "text/plain"}}}]}
                        },
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


class _DimEmbeddings(_Embeddings):
    def __init__(self, dims, name="model-x"):
        self.dimension = dims
        self.model_name = name


def test_ensure_index_creates_index_sized_for_model():
    client = MagicMock()
    client.indices.exists.return_value = False
    store = ElasticsearchVectorStore("idx", _DimEmbeddings(3072, "text-embedding-3-large"), client=client)

    assert store.ensure_index() is True
    body = client.indices.create.call_args.kwargs
    assert body["mappings"]["properties"]["embedding"]["dims"] == 3072
    assert body["mappings"]["properties"]["document_id"] == {"type": "keyword"}
    assert body["mappings"]["_meta"]["embedding_model"] == "text-embedding-3-large"


def test_ensure_index_accepts_matching_existing_index():
    client = MagicMock()
    client.indices.exists.return_value = True
    client.indices.get_mapping.return_value = {"idx": {"mappings": {"properties": {"embedding": {"dims": 768}}}}}
    store = ElasticsearchVectorStore("idx", _DimEmbeddings(768), client=client)
    assert store.ensure_index() is False
    client.indices.create.assert_not_called()


def test_ensure_index_rejects_dimension_mismatch():
    client = MagicMock()
    client.indices.exists.return_value = True
    client.indices.get_mapping.return_value = {"idx": {"mappings": {"properties": {"embedding": {"dims": 768}}}}}
    store = ElasticsearchVectorStore("idx", _DimEmbeddings(3072), client=client)
    with pytest.raises(IndexDimensionMismatchError):
        store.ensure_index()


def test_health_reports_degraded_when_elasticsearch_fails(client, fakes):
    fakes["vector_store"].health_check = lambda: (_ for _ in ()).throw(ConnectionError("down"))
    response = client.get("/health")
    assert response.status_code == 503
    assert response.get_json()["status"] == "degraded"


def test_health_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_reciprocal_rank_fusion_rewards_hits_in_both_lists():
    from src.vector_store.elasticsearch_store import reciprocal_rank_fusion

    dense = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    sparse = [{"id": "c"}, {"id": "d"}]
    fused = reciprocal_rank_fusion([dense, sparse], k=3)
    # b and d tie (both rank 2); ties keep the order of the first list
    assert [hit["id"] for hit in fused] == ["c", "a", "b"]
    assert fused[0]["score"] == pytest.approx(1 / 63 + 1 / 61)


def _hit(chunk_id):
    return {"_id": chunk_id, "_score": 1.0, "_source": {"id": chunk_id, "content": chunk_id, "metadata": {}, "source": "s"}}


def test_search_modes_issue_the_right_queries(store, monkeypatch):
    store.embedding_service.embed_query = lambda text: [0.1]
    store.client.search.side_effect = lambda index, body: {"hits": {"hits": [_hit("x")]}}

    store.similarity_search("q", k=2, mode="dense", filters={"team": "a"})
    body = store.client.search.call_args.kwargs["body"]
    assert body["knn"]["filter"] == {"bool": {"must": [{"term": {"metadata.team": "a"}}]}}

    store.similarity_search("q", k=2, mode="sparse", filters={"team": "a"})
    body = store.client.search.call_args.kwargs["body"]
    assert body["query"]["bool"]["must"] == [{"match": {"content": {"query": "q"}}}]
    assert body["query"]["bool"]["filter"] == [{"bool": {"must": [{"term": {"metadata.team": "a"}}]}}]

    store.client.search.reset_mock()
    results = store.similarity_search("q", k=2, mode="hybrid")
    assert store.client.search.call_count == 2
    assert [r["id"] for r in results] == ["x"]


def test_default_mode_comes_from_env(store, monkeypatch):
    store.embedding_service.embed_query = lambda text: [0.1]
    store.client.search.side_effect = lambda index, body: {"hits": {"hits": []}}
    monkeypatch.setenv("SEARCH_MODE", "sparse")
    store.similarity_search("q", k=2)
    assert "query" in store.client.search.call_args.kwargs["body"]


def test_unknown_mode_is_rejected(store):
    with pytest.raises(ValueError):
        store.similarity_search("q", mode="magic")


def test_search_mode_is_forwarded_by_the_api(client, auth_headers, fakes):
    client.post("/query", json={"query": "q", "search_mode": "sparse"}, headers=auth_headers)
    assert fakes["rag_service"].calls[-1]["search_mode"] == "sparse"
    assert client.post("/query", json={"query": "q", "search_mode": "x"}, headers=auth_headers).status_code == 422
