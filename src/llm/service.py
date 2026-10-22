"""LLM provider abstraction with multiple backend support."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any, Optional

from tenacity import retry, stop_after_attempt, wait_exponential

DEFAULT_MODELS = {
    "openai": "gpt-4.1",
    "anthropic": "claude-opus-5-5",
    "gemini": "gemini-2.5-flash",
}


def _default_model(provider: str) -> str:
    return os.getenv(f"{provider.upper()}_MODEL") or DEFAULT_MODELS[provider]


def _timeout() -> float:
    return float(os.getenv("LLM_TIMEOUT_SECONDS", "120"))


def _max_retries() -> int:
    return int(os.getenv("LLM_MAX_RETRIES", "2"))


class LLMRefusalError(RuntimeError):
    """Raised when the provider declines to answer for policy reasons."""


class LLMProvider(ABC):
    """Abstract base for LLM providers."""

    @abstractmethod
    def generate(
        self,
        prompt: str,
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> str:
        """Generate completion from prompt."""

    @abstractmethod
    def stream_generate(
        self,
        prompt: str,
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ):
        """Stream completion tokens."""


class OpenAILLMProvider(LLMProvider):
    """OpenAI GPT provider."""

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "openai package is required for OpenAILLMProvider. " "Install with: pip install openai"
            ) from exc

        self.model = model or _default_model("openai")
        self.client = OpenAI(
            api_key=api_key or os.getenv("OPENAI_API_KEY"),
            timeout=_timeout(),
            max_retries=_max_retries(),
        )

    def generate(
        self,
        prompt: str,
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        return response.choices[0].message.content

    def stream_generate(
        self,
        prompt: str,
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ):
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            **kwargs,
        )
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


class AnthropicLLMProvider(LLMProvider):
    """Anthropic Claude provider.

    Current Claude models reject sampling parameters, so ``temperature`` is
    accepted for interface compatibility but not sent. Policy refusals are
    retried server-side on a fallback model (``fallbacks="default"``); if the
    whole chain refuses, :class:`LLMRefusalError` is raised.
    """

    FALLBACK_BETA = "server-side-fallback-2026-07-01"

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        try:
            from anthropic import Anthropic
        except ImportError as exc:
            raise RuntimeError(
                "anthropic package is required for AnthropicLLMProvider. " "Install with: pip install anthropic"
            ) from exc

        self.model = model or _default_model("anthropic")
        self.client = Anthropic(
            api_key=api_key or os.getenv("ANTHROPIC_API_KEY"),
            timeout=_timeout(),
            max_retries=_max_retries(),
        )

    def _request(self, prompt: str, max_tokens: Optional[int], default_max_tokens: int, **kwargs: Any) -> dict:
        return {
            "model": self.model,
            "max_tokens": max_tokens or default_max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "betas": [self.FALLBACK_BETA],
            "fallbacks": "default",
            **kwargs,
        }

    def generate(
        self,
        prompt: str,
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> str:
        response = self.client.beta.messages.create(**self._request(prompt, max_tokens, 16000, **kwargs))
        if response.stop_reason == "refusal":
            raise LLMRefusalError("The model declined to answer this request")
        return "".join(block.text for block in response.content if block.type == "text")

    def stream_generate(
        self,
        prompt: str,
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ):
        with self.client.beta.messages.stream(**self._request(prompt, max_tokens, 64000, **kwargs)) as stream:
            for text in stream.text_stream:
                yield text
            if stream.get_final_message().stop_reason == "refusal":
                raise LLMRefusalError("The model declined to answer this request")


class GeminiLLMProvider(LLMProvider):
    """Google Gemini provider."""

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        try:
            from google import genai
        except ImportError as exc:
            raise RuntimeError(
                "google-genai package is required for GeminiLLMProvider. " "Install with: pip install google-genai"
            ) from exc

        self.model = model or _default_model("gemini")
        api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY must be set in environment or passed as argument")

        self.client = genai.Client(api_key=api_key, http_options={"timeout": int(_timeout() * 1000)})

    def _config(self, temperature: float, max_tokens: Optional[int]) -> dict:
        generation_config = {"temperature": temperature}
        if max_tokens:
            generation_config["max_output_tokens"] = max_tokens
        return generation_config

    @retry(stop=stop_after_attempt(_max_retries() + 1), wait=wait_exponential(multiplier=1, min=1, max=10), reraise=True)
    def generate(
        self,
        prompt: str,
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> str:
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=self._config(temperature, max_tokens),
        )
        return response.text

    def stream_generate(
        self,
        prompt: str,
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ):
        response = self.client.models.generate_content_stream(
            model=self.model,
            contents=prompt,
            config=self._config(temperature, max_tokens),
        )
        for chunk in response:
            if chunk.text:
                yield chunk.text


class LLMService:
    """Facade for LLM generation with provider abstraction."""

    def __init__(self, provider: Optional[LLMProvider] = None) -> None:
        self.provider = provider or self._default_provider()

    def generate(
        self,
        prompt: str,
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> str:
        return self.provider.generate(prompt, temperature=temperature, max_tokens=max_tokens, **kwargs)

    def stream_generate(
        self,
        prompt: str,
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ):
        return self.provider.stream_generate(prompt, temperature=temperature, max_tokens=max_tokens, **kwargs)

    def _default_provider(self) -> LLMProvider:
        """Create default provider based on environment."""
        provider_name = os.getenv("LLM_PROVIDER", "gemini").lower()

        if provider_name == "openai":
            return OpenAILLMProvider()
        elif provider_name == "anthropic":
            return AnthropicLLMProvider()
        elif provider_name == "gemini":
            return GeminiLLMProvider()
        else:
            raise ValueError(f"Unknown LLM provider: {provider_name}. " "Supported providers: openai, anthropic, gemini")
