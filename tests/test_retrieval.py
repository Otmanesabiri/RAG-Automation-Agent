"""Re-ranking, citation verification and prompt templates."""

import pytest

from src.llm.prompts import PromptBuilder, build_prompt
from src.retrieval.citation_verifier import CitationVerifier
from src.retrieval.reranker import CrossEncoderReranker

SOURCES = [
    {"id": "c1", "content": "Machine learning is a subset of artificial intelligence.", "source": "ml.txt", "score": 0.4},
    {"id": "c2", "content": "Paris is the capital of France.", "source": "geo.txt", "score": 0.9},
]


class _FakeCrossEncoder:
    def predict(self, pairs):
        return [1.0 if "learning" in content else 0.1 for _, content in pairs]


def _reranker(model):
    reranker = CrossEncoderReranker(device="cpu")
    reranker._model = model
    return reranker


def test_reranker_orders_by_cross_encoder_score_and_keeps_ids():
    ranked = _reranker(_FakeCrossEncoder()).rerank("what is machine learning", SOURCES, top_k=1)
    assert [r.document_id for r in ranked] == ["c1"]
    assert ranked[0].rerank_score == 1.0
    assert ranked[0].original_score == 0.4


def test_reranker_falls_back_to_original_order_on_error():
    class _Broken:
        def predict(self, pairs):
            raise RuntimeError("model failure")

    ranked = _reranker(_Broken()).rerank("q", SOURCES)
    assert [r.document_id for r in ranked] == ["c1", "c2"]
    assert [r.rerank_score for r in ranked] == [0.4, 0.9]


def test_reranker_handles_empty_input():
    assert _reranker(_FakeCrossEncoder()).rerank("q", []) == []


def test_grounded_answer_is_accepted():
    check = CitationVerifier().verify("Paris is the capital of France.", SOURCES)
    assert check.is_grounded
    assert check.confidence == 1.0


def test_invented_answer_is_flagged():
    check = CitationVerifier().verify("The moon is made of green cheese and orbits Jupiter.", SOURCES)
    assert not check.is_grounded
    assert check.ungrounded_claims
    assert check.warnings


def test_no_sources_is_not_grounded():
    check = CitationVerifier().verify("anything", [])
    assert not check.is_grounded
    assert check.confidence == 0.0


@pytest.mark.parametrize("prompt_type", list(PromptBuilder.PROMPT_TYPES))
def test_every_prompt_type_includes_question_and_context(prompt_type):
    prompt = build_prompt(prompt_type, query="What is ML?", context="[1] ML is AI.", sources=SOURCES)
    assert "What is ML?" in prompt
    assert "ML is AI." in prompt or "Machine learning" in prompt


def test_unknown_prompt_type_raises():
    with pytest.raises(ValueError):
        PromptBuilder.get_prompt("nope")


def test_custom_prompt():
    prompt = PromptBuilder.create_custom_prompt("Be brief.").format(query="Q?", context="C.")
    assert prompt.startswith("Be brief.")
    assert "Q?" in prompt and "C." in prompt
