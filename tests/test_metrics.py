"""Prometheus counters."""

from prometheus_client import REGISTRY

from src.embeddings.service import EmbeddingService


def _value(name):
    return REGISTRY.get_sample_value(name) or 0.0


def test_ingestion_counter_counts_documents(client, auth_headers):
    before = _value("rag_agent_ingestion_documents_total")
    payload = {
        "source": "upload",
        "documents": [{"content": "a", "filename": "a.txt"}, {"content": "b", "filename": "b.txt"}],
    }
    assert client.post("/ingest", json=payload, headers=auth_headers).status_code == 202
    assert _value("rag_agent_ingestion_documents_total") == before + 2


def test_failed_ingestion_is_not_counted(client, auth_headers):
    before = _value("rag_agent_ingestion_documents_total")
    client.post("/ingest", json={"source": "url", "documents": [{"url": "http://127.0.0.1/"}]}, headers=auth_headers)
    assert _value("rag_agent_ingestion_documents_total") == before


def test_embedding_tokens_are_recorded():
    class _Provider:
        def embed_documents(self, texts):
            return [[0.0] for _ in texts]

        def embed_query(self, text):
            return [0.0]

    service = EmbeddingService(provider=_Provider())
    before = _value("rag_agent_embedding_tokens_total")
    service.embed_documents(["x" * 400])
    service.embed_query("y" * 40)
    assert _value("rag_agent_embedding_tokens_total") == before + 110


def test_query_counter(client, auth_headers):
    before = _value("rag_agent_queries_total")
    client.post("/query", json={"query": "q"}, headers=auth_headers)
    assert _value("rag_agent_queries_total") == before + 1
