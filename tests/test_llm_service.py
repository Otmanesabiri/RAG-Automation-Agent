"""LLM provider configuration and Anthropic request shape."""

from types import SimpleNamespace

import pytest

from src.llm import service
from src.llm.service import AnthropicLLMProvider, LLMRefusalError, LLMService


class _FakeBetaMessages:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def _anthropic_provider(response):
    provider = AnthropicLLMProvider.__new__(AnthropicLLMProvider)
    provider.model = "claude-opus-5-5"
    messages = _FakeBetaMessages(response)
    provider.client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    return provider, messages


def _response(stop_reason="end_turn", texts=("Hello", " world")):
    content = [SimpleNamespace(type="thinking", thinking="")]
    content += [SimpleNamespace(type="text", text=t) for t in texts]
    return SimpleNamespace(stop_reason=stop_reason, content=content)


def test_anthropic_request_omits_temperature_and_enables_fallbacks():
    provider, messages = _anthropic_provider(_response())
    answer = provider.generate("question", temperature=0.2)

    assert answer == "Hello world"
    assert "temperature" not in messages.kwargs
    assert messages.kwargs["fallbacks"] == "default"
    assert messages.kwargs["betas"] == [AnthropicLLMProvider.FALLBACK_BETA]
    assert messages.kwargs["max_tokens"] == 16000


def test_anthropic_refusal_raises():
    provider, _ = _anthropic_provider(_response(stop_reason="refusal", texts=()))
    with pytest.raises(LLMRefusalError):
        provider.generate("question")


def test_model_can_be_overridden_by_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
    assert service._default_model("anthropic") == "claude-sonnet-5-5"
    monkeypatch.delenv("ANTHROPIC_MODEL")
    assert service._default_model("anthropic") == "claude-opus-5-5"


def test_default_provider_is_gemini(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    created = []
    monkeypatch.setattr(service, "GeminiLLMProvider", lambda: created.append("gemini") or "gemini-provider")
    assert LLMService().provider == "gemini-provider"


def test_refusal_maps_to_422(client, auth_headers, fakes):
    def refuse(*args, **kwargs):
        raise LLMRefusalError("declined")

    fakes["rag_service"].query = refuse
    response = client.post("/query", json={"query": "x"}, headers=auth_headers)
    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "llm_refusal"
