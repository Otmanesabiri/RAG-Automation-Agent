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
        return [
            {"id": f"c{i}", "document_id": "d", "content": f"text {i}", "source": "s", "score": 1.0 - i / 10} for i in range(k)
        ]


class _Reranker:
    def rerank(self, question, docs, top_k):
        ranked = list(reversed(docs))[:top_k]
        return [
            SimpleNamespace(
                document_id=d["id"],
                content=d["content"],
                snippet=d["content"],
                source=d["source"],
                rerank_score=9.0,
                metadata={},
            )
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


class _RecordingLLM(_LLM):
    def __init__(self, standalone="What are the drawbacks of solar panels?"):
        self.prompts = []
        self.standalone = standalone

    def generate(self, prompt, **kwargs):
        self.prompts.append(prompt)
        if "Standalone question:" in prompt:
            return self.standalone
        return "answer"


class _QueryRecordingStore(_Store):
    def similarity_search(self, query, k, **kwargs):
        self.query = query
        return super().similarity_search(query, k, **kwargs)


HISTORY = [
    {"role": "user", "content": "Tell me about solar panels"},
    {"role": "assistant", "content": "Solar panels convert sunlight into electricity."},
]


def test_follow_up_is_condensed_for_retrieval_and_history_is_in_prompt():
    service = _service()
    service.llm_service = _RecordingLLM()
    service.vector_store = _QueryRecordingStore()

    result = service.query("and their drawbacks?", top_k=1, rerank=False, history=HISTORY)

    assert service.vector_store.query == "What are the drawbacks of solar panels?"
    assert result["metadata"]["search_query"] == "What are the drawbacks of solar panels?"
    final_prompt = service.llm_service.prompts[-1]
    assert "User: Tell me about solar panels" in final_prompt
    assert "and their drawbacks?" in final_prompt


def test_no_history_means_no_condensation():
    service = _service()
    service.llm_service = _RecordingLLM()
    service.vector_store = _QueryRecordingStore()
    service.query("standalone question", top_k=1, rerank=False)
    assert service.vector_store.query == "standalone question"
    assert len(service.llm_service.prompts) == 1


def test_condensation_can_be_disabled(monkeypatch):
    monkeypatch.setenv("QUERY_CONDENSATION", "false")
    service = _service()
    service.llm_service = _RecordingLLM()
    service.vector_store = _QueryRecordingStore()
    service.query("and their drawbacks?", top_k=1, rerank=False, history=HISTORY)
    assert service.vector_store.query == "and their drawbacks?"


def test_format_history_respects_limits():
    from src.llm.rag_service import format_history

    history = [{"role": "user", "content": f"message {i}"} for i in range(10)]
    text = format_history(history, max_messages=3, max_chars=1000)
    assert text.splitlines() == ["User: message 7", "User: message 8", "User: message 9"]
    assert format_history(history, max_messages=3, max_chars=20) == "User: message 9"


def test_route_passes_previous_messages(client, auth_headers, fakes):
    first = client.post("/query", json={"query": "first"}, headers=auth_headers).get_json()
    session_id = first["metadata"]["session_id"]
    client.post("/query", json={"query": "second", "session_id": session_id}, headers=auth_headers)

    history = fakes["rag_service"].calls[-1]["history"]
    assert [m["content"] for m in history] == ["first", "answer to first"]


class _ExpandingLLM(_LLM):
    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    def generate(self, prompt, **kwargs):
        self.prompts.append(prompt)
        if "search queries" in prompt or "factual passage" in prompt:
            if isinstance(self.reply, Exception):
                raise self.reply
            return self.reply
        return "answer"


class _MultiStore(_Store):
    def __init__(self):
        super().__init__()
        self.queries = []

    def similarity_search(self, query, k, **kwargs):
        self.queries.append(query)
        return [{"id": f"{query}-{i}", "document_id": "d", "content": query, "source": "s", "score": 1.0} for i in range(k)]


def test_multi_query_searches_each_reformulation_and_fuses():
    service = _service()
    service.reranker = None
    service.vector_store = _MultiStore()
    service.llm_service = _ExpandingLLM("1. solar panel downsides\n- disadvantages of photovoltaics\n\nsolar drawbacks?")

    result = service.query("solar drawbacks?", top_k=3, query_expansion="multi_query")

    assert service.vector_store.queries == ["solar drawbacks?", "solar panel downsides", "disadvantages of photovoltaics"]
    assert result["metadata"]["expanded_queries"] == ["solar panel downsides", "disadvantages of photovoltaics"]
    assert len(result["sources"]) == 3
    assert {s["chunk_id"].rsplit("-", 1)[0] for s in result["sources"]} == set(service.vector_store.queries)


def test_hyde_searches_the_hypothetical_passage():
    service = _service()
    service.reranker = None
    service.vector_store = _MultiStore()
    service.llm_service = _ExpandingLLM("Solar panels lose efficiency in heat and cost a lot upfront.")
    service.query("solar drawbacks?", top_k=2, query_expansion="hyde")
    assert service.vector_store.queries[1].startswith("Solar panels lose efficiency")


def test_expansion_failure_falls_back_to_the_question():
    service = _service()
    service.reranker = None
    service.vector_store = _MultiStore()
    service.llm_service = _ExpandingLLM(RuntimeError("LLM down"))
    result = service.query("q", top_k=1, query_expansion="multi_query")
    assert service.vector_store.queries == ["q"]
    assert result["metadata"]["expanded_queries"] == []


def test_expansion_defaults_to_env(monkeypatch):
    service = _service()
    service.llm_service = _ExpandingLLM("alt query")
    assert service.expand_query("q", None) == []
    monkeypatch.setenv("QUERY_EXPANSION", "multi_query")
    assert service.expand_query("q", None) == ["alt query"]


def test_query_expansion_is_forwarded_by_the_api(client, auth_headers, fakes):
    client.post("/query", json={"query": "q", "query_expansion": "hyde"}, headers=auth_headers)
    assert fakes["rag_service"].calls[-1]["query_expansion"] == "hyde"
