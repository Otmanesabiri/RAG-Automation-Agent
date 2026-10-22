"""Query options, SSE streaming, re-ranking toggle and ingestion options."""

import json
from types import SimpleNamespace

from src.ingestion.chunker import ChunkingService
from src.ingestion.config import ChunkingConfig
from src.llm.rag_service import RAGService


def _parse_sse(body: str):
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_query_options_are_forwarded(client, auth_headers, fakes):
    payload = {
        "query": "q",
        "rerank": False,
        "prompt_type": "citation",
        "min_score": 0.5,
        "max_age_days": 7,
        "filters": {"attributes": {"category": "hr"}},
    }
    assert client.post("/query", json=payload, headers=auth_headers).status_code == 200
    call = fakes["rag_service"].calls[-1]
    assert call["rerank"] is False
    assert call["prompt_type"] == "citation"
    assert call["min_score"] == 0.5
    assert call["max_age_days"] == 7
    assert call["filters"] == {"category": "hr"}


def test_invalid_prompt_type_is_rejected(client, auth_headers):
    response = client.post("/query", json={"query": "q", "prompt_type": "nope"}, headers=auth_headers)
    assert response.status_code == 422


def test_stream_returns_sse_and_records_answer(client, auth_headers, fakes):
    def stream_query(question, **kwargs):
        yield {"event": "sources", "data": [{"document_id": "d"}]}
        yield {"event": "token", "data": "Hel"}
        yield {"event": "token", "data": "lo"}
        yield {"event": "done", "data": {"answer": "Hello", "metadata": {"top_k": 4}}}

    fakes["rag_service"].stream_query = stream_query
    response = client.post("/query", json={"query": "q", "stream": True}, headers=auth_headers)

    assert response.mimetype == "text/event-stream"
    events = _parse_sse(response.get_data(as_text=True))
    assert [name for name, _ in events] == ["sources", "token", "token", "done"]
    done = events[-1][1]
    session_id = done["metadata"]["session_id"]
    messages = client.get(f"/sessions/{session_id}", headers=auth_headers).get_json()["messages"]
    assert messages[-1] == {**messages[-1], "role": "assistant", "content": "Hello"}


def test_stream_error_is_reported_in_band(client, auth_headers, fakes):
    def stream_query(question, **kwargs):
        yield {"event": "sources", "data": []}
        raise RuntimeError("secret detail")

    fakes["rag_service"].stream_query = stream_query
    body = client.post("/query", json={"query": "q", "stream": True}, headers=auth_headers).get_data(as_text=True)
    events = _parse_sse(body)
    assert events[-1][0] == "error"
    assert "secret detail" not in body


class _Store:
    def __init__(self):
        self.k = None

    def similarity_search(self, query, k, **kwargs):
        self.k = k
        return [{"id": f"c{i}", "document_id": "d", "content": f"text {i}", "source": "s", "score": 1.0 - i / 10} for i in range(k)]


class _Reranker:
    def rerank(self, question, docs, top_k):
        ranked = list(reversed(docs))[:top_k]
        return [
            SimpleNamespace(document_id=d["id"], content=d["content"], snippet=d["content"], source=d["source"], rerank_score=9.0, metadata={})
            for d in ranked
        ]


class _LLM:
    def generate(self, prompt, **kwargs):
        return "answer"

    def stream_generate(self, prompt, **kwargs):
        yield "ans"
        yield "wer"


def _service():
    service = RAGService.__new__(RAGService)
    service.vector_store = _Store()
    service.llm_service = _LLM()
    service.prompt_template = "{context}{question}"
    service.prompt_type = "strict"
    service.rerank_top_k = 10
    service.reranker = _Reranker()
    service.citation_verifier = None
    return service


def test_rerank_toggle_per_query():
    service = _service()
    with_rerank = service.query("q", top_k=2)
    assert service.vector_store.k == 10
    assert with_rerank["metadata"]["reranking_enabled"] is True
    assert [s["chunk_id"] for s in with_rerank["sources"]] == ["c9", "c8"]
    assert with_rerank["sources"][0]["document_id"] == "d"

    without = service.query("q", top_k=2, rerank=False)
    assert service.vector_store.k == 2
    assert without["metadata"]["reranking_enabled"] is False


def test_stream_query_events():
    events = list(_service().stream_query("q", top_k=1, rerank=False))
    assert [e["event"] for e in events] == ["sources", "token", "token", "done"]
    assert events[-1]["data"]["answer"] == "answer"


def test_explicit_chunking_config_bypasses_heuristics():
    doc = SimpleNamespace(page_content="word " * 400, metadata={"source": "s", "page_count": 2})
    chunks = ChunkingService().split_documents([doc], config=ChunkingConfig(chunk_size=300, chunk_overlap=0))
    assert all(c.metadata["chunk_size"] == 300 for c in chunks)


def test_ingest_options_are_applied(client, auth_headers, fakes):
    payload = {
        "source": "upload",
        "documents": [{"content": "word " * 400, "filename": "a.txt"}],
        "options": {"chunk_size": 300, "chunk_overlap": 30},
    }
    assert client.post("/ingest", json=payload, headers=auth_headers).status_code == 202
    assert {c.metadata["chunk_size"] for c in fakes["vector_store"].indexed} == {300}


def test_ingest_options_validation(client, auth_headers):
    payload = {
        "source": "upload",
        "documents": [{"content": "x", "filename": "a.txt"}],
        "options": {"chunk_size": 200, "chunk_overlap": 500},
    }
    assert client.post("/ingest", json=payload, headers=auth_headers).status_code == 422
