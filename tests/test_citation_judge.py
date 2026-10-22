"""LLM-judge citation verification and its wiring into the RAG service and API."""

import pytest
from prometheus_client import REGISTRY

from src.llm.rag_service import RAGService
from src.retrieval.llm_judge import LLMCitationVerifier, _parse_claims

SOURCES = [{"id": "c1", "content": "Paris is the capital of France.", "source": "geo.txt", "score": 1.0}]


class _JudgeLLM:
    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    def generate(self, prompt, **kwargs):
        self.prompts.append(prompt)
        if "You check whether an answer is supported" in prompt:
            if isinstance(self.reply, Exception):
                raise self.reply
            return self.reply
        return "Paris is the capital. It has 90 million inhabitants."


MIXED = """```json
{"claims": [
  {"claim": "Paris is the capital of France", "supported": true, "sources": [1]},
  {"claim": "Paris has 90 million inhabitants", "supported": false, "sources": []}
]}
```"""


def test_judge_scores_claims():
    check = LLMCitationVerifier(_JudgeLLM(MIXED)).verify("answer", SOURCES)
    assert check.confidence == 0.5
    assert not check.is_grounded
    assert check.grounded_claims == ["Paris is the capital of France"]
    assert check.source_matches == {"Paris is the capital of France": [0]}
    assert "Paris has 90 million inhabitants" in check.ungrounded_claims


def test_judge_prompt_contains_numbered_sources_and_answer():
    llm = _JudgeLLM('{"claims": []}')
    check = LLMCitationVerifier(llm).verify("my answer", SOURCES)
    assert "[1] Paris is the capital of France." in llm.prompts[0]
    assert "my answer" in llm.prompts[0]
    assert check.is_grounded and check.confidence == 1.0


def test_no_sources_is_not_grounded():
    check = LLMCitationVerifier(_JudgeLLM(MIXED)).verify("answer", [])
    assert not check.is_grounded


@pytest.mark.parametrize("reply", ["not json", '{"verdict": "ok"}'])
def test_unparseable_judge_reply_raises(reply):
    with pytest.raises(ValueError):
        _parse_claims(reply)


class _Store:
    def similarity_search(self, query, k, **kwargs):
        return SOURCES[:k]


def _service(llm, mode=None):
    return RAGService(_Store(), llm, enable_reranking=False, citation_check_mode=mode)


def _metric(name, mode):
    return REGISTRY.get_sample_value(name, {"mode": mode}) or 0.0


def test_rag_service_reports_llm_citation_check():
    before = _metric("rag_agent_ungrounded_answers_total", "llm")
    result = _service(_JudgeLLM(MIXED), "llm").query("capital?", top_k=1)
    assert result["citation_check"]["mode"] == "llm"
    assert result["citation_check"]["confidence"] == 0.5
    assert result["citation_check"]["ungrounded_claims"] == ["Paris has 90 million inhabitants"]
    assert _metric("rag_agent_ungrounded_answers_total", "llm") == before + 1


def test_per_query_mode_overrides_default():
    service = _service(_JudgeLLM(MIXED), "off")
    assert "citation_check" not in service.query("q", top_k=1)
    assert service.query("q", top_k=1, verify_citations="llm")["citation_check"]["mode"] == "llm"
    assert service.query("q", top_k=1, verify_citations="lexical")["citation_check"]["mode"] == "lexical"


def test_judge_failure_does_not_break_the_answer():
    result = _service(_JudgeLLM(RuntimeError("judge down")), "llm").query("q", top_k=1)
    assert result["answer"]
    assert "citation_check" not in result


def test_default_mode_from_env(monkeypatch):
    monkeypatch.setenv("CITATION_CHECK", "lexical")
    assert _service(_JudgeLLM(MIXED)).citation_check_mode == "lexical"
    monkeypatch.setenv("CITATION_CHECK", "bogus")
    with pytest.raises(ValueError):
        _service(_JudgeLLM(MIXED))


def test_api_returns_citation_check(client, auth_headers, fakes):
    original = fakes["rag_service"].query

    def query(question, **kwargs):
        return {**original(question, **kwargs), "citation_check": {"mode": "llm", "confidence": 1.0}}

    fakes["rag_service"].query = query
    body = client.post("/query", json={"query": "q", "verify_citations": "llm"}, headers=auth_headers).get_json()
    assert body["citation_check"] == {"mode": "llm", "confidence": 1.0}
    assert fakes["rag_service"].calls[-1]["verify_citations"] == "llm"
