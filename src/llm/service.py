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


class GeminiLLMProvider(LLMProvider):
    """Google Gemini provider."""

    def __init__(
        self,
        model: str = "gemini-2.0-flash-exp",
        api_key: Optional[str] = None,
    ) -> None:
        try:
            from google import genai
        except ImportError as exc:
            raise RuntimeError(
                "google-genai package is required for GeminiLLMProvider. "
                "Install with: pip install google-genai"
            ) from exc

        self.model = model
        # Set API key in environment if provided
        if api_key:
            os.environ["GEMINI_API_KEY"] = api_key
        elif not os.getenv("GEMINI_API_KEY"):
            raise ValueError("GEMINI_API_KEY must be set in environment or passed as argument")
        
        self.client = genai.Client()

    def generate(
        self,
        prompt: str,
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> str:
        # Build generation config
        generation_config = {
            "temperature": temperature,
        }
        if max_tokens:
            generation_config["max_output_tokens"] = max_tokens
        
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=generation_config,
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
        # Build generation config
        generation_config = {
            "temperature": temperature,
        }
        if max_tokens:
            generation_config["max_output_tokens"] = max_tokens
        
        response = self.client.models.generate_content_stream(
            model=self.model,
            contents=prompt,
            config=generation_config,
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
        provider_name = os.getenv("LLM_PROVIDER", "openai").lower()

        if provider_name == "openai":
            return OpenAILLMProvider()
        elif provider_name == "anthropic":
            return AnthropicLLMProvider()
        elif provider_name == "gemini":
            return GeminiLLMProvider()
        else:
            raise ValueError(
                f"Unknown LLM provider: {provider_name}. "
                "Supported providers: openai, anthropic, gemini"
            )
