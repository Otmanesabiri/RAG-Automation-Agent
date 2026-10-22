"""Prometheus counters."""

import pytest
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


def _labelled(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_llm_usage_and_cost(monkeypatch):
    from src.observability.metrics import record_llm_usage

    before_in = _labelled("rag_agent_llm_tokens_total", provider="anthropic", model="claude-opus-5-5", kind="input")
    before_cost = _labelled("rag_agent_llm_cost_usd_total", provider="anthropic", model="claude-opus-5-5")
    record_llm_usage("anthropic", "claude-opus-5-5", 1_000_000, 100_000)
    assert (
        _labelled("rag_agent_llm_tokens_total", provider="anthropic", model="claude-opus-5-5", kind="input")
        == before_in + 1_000_000
    )
    # 1M input * $4 + 0.1M output * $20
    assert _labelled("rag_agent_llm_cost_usd_total", provider="anthropic", model="claude-opus-5-5") == pytest.approx(
        before_cost + 6.0
    )

    monkeypatch.setenv("LLM_PRICES", '{"custom-model": [1.0, 2.0]}')
    record_llm_usage("openai", "custom-model", 500_000, 500_000)
    assert _labelled("rag_agent_llm_cost_usd_total", provider="openai", model="custom-model") == pytest.approx(1.5)

    record_llm_usage("gemini", "unpriced-model", 10, 10)
    assert REGISTRY.get_sample_value("rag_agent_llm_cost_usd_total", {"provider": "gemini", "model": "unpriced-model"}) is None


def test_providers_report_usage(monkeypatch):
    from types import SimpleNamespace

    from src.llm import service

    calls = []
    monkeypatch.setattr(service, "record_llm_usage", lambda *args: calls.append(args))

    anthropic = service.AnthropicLLMProvider.__new__(service.AnthropicLLMProvider)
    anthropic.model = "claude-opus-5-5"
    anthropic._record_usage(SimpleNamespace(usage=SimpleNamespace(input_tokens=10, output_tokens=5), model="claude-opus-4-8"))
    gemini = service.GeminiLLMProvider.__new__(service.GeminiLLMProvider)
    gemini.model = "gemini-2.5-flash"
    gemini._record_usage(SimpleNamespace(prompt_token_count=7, candidates_token_count=3))

    assert calls == [("anthropic", "claude-opus-4-8", 10, 5), ("gemini", "gemini-2.5-flash", 7, 3)]


@pytest.mark.parametrize(
    "answer,expected",
    [
        ("The context does not contain information about pricing.", True),
        ("Je ne sais pas, les documents ne mentionnent pas ce point.", True),
        ("I don't know.", True),
        ("Paris is the capital of France [1].", False),
    ],
)
def test_no_information_detection(answer, expected):
    from src.observability.metrics import is_no_information_answer

    assert is_no_information_answer(answer) is expected


def test_stage_timer_and_answer_counters():
    from src.observability.metrics import record_answer, stage_timer

    before = REGISTRY.get_sample_value("rag_agent_stage_duration_seconds_count", {"stage": "unit-test"}) or 0.0
    with stage_timer("unit-test"):
        pass
    assert REGISTRY.get_sample_value("rag_agent_stage_duration_seconds_count", {"stage": "unit-test"}) == before + 1

    before = _labelled("rag_agent_answers_total", outcome="no_information")
    record_answer("I don't know", 0)
    assert _labelled("rag_agent_answers_total", outcome="no_information") == before + 1


def test_query_records_stage_latencies(client, auth_headers):
    from src.llm.rag_service import RAGService

    class _Store:
        def similarity_search(self, query, k, **kwargs):
            return [{"id": "c", "content": "text", "source": "s", "score": 1.0}]

    class _LLM:
        def generate(self, prompt, **kwargs):
            return "answer"

    stages = ("retrieval", "generation")
    before = {s: REGISTRY.get_sample_value("rag_agent_stage_duration_seconds_count", {"stage": s}) or 0.0 for s in stages}
    RAGService(_Store(), _LLM(), enable_reranking=False).query("q", top_k=1)
    for stage in stages:
        assert REGISTRY.get_sample_value("rag_agent_stage_duration_seconds_count", {"stage": stage}) == before[stage] + 1


def test_tracing_is_a_no_op_without_endpoint(monkeypatch):
    from flask import Flask

    from src.observability import tracing

    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert tracing.configure_tracing(Flask("t")) is False
    with tracing.span("anything"):
        pass
