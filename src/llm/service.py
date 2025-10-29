"""LLM provider abstraction with multiple backend support."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


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
        model: str = "gpt-4o",
        api_key: Optional[str] = None,
    ) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "openai package is required for OpenAILLMProvider. "
                "Install with: pip install openai"
            ) from exc

        self.model = model
        self.client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))

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
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


class AnthropicLLMProvider(LLMProvider):
    """Anthropic Claude provider."""

    def __init__(
        self,
        model: str = "claude-3-5-sonnet-20241022",
        api_key: Optional[str] = None,
    ) -> None:
        try:
            from anthropic import Anthropic
        except ImportError as exc:
            raise RuntimeError(
                "anthropic package is required for AnthropicLLMProvider. "
                "Install with: pip install anthropic"
            ) from exc

        self.model = model
        self.client = Anthropic(api_key=api_key or os.getenv("ANTHROPIC_API_KEY"))

    def generate(
        self,
        prompt: str,
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens or 4096,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            **kwargs,
        )
        return response.content[0].text

    def stream_generate(
        self,
        prompt: str,
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ):
        with self.client.messages.stream(
            model=self.model,
            max_tokens=max_tokens or 4096,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            **kwargs,
        ) as stream:
            for text in stream.text_stream:
                yield text


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
        """Select provider based on environment configuration."""
        provider_name = os.getenv("LLM_PROVIDER", "openai").lower()
        if provider_name == "openai":
            model = os.getenv("OPENAI_MODEL", "gpt-4o")
            return OpenAILLMProvider(model=model)
        elif provider_name == "anthropic":
            model = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
            return AnthropicLLMProvider(model=model)
        else:
            raise ValueError(f"Unknown LLM provider: {provider_name}")
