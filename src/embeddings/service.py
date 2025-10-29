"""Embedding generation service with multiple provider support."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import List, Optional

from tenacity import retry, stop_after_attempt, wait_exponential


class EmbeddingProvider(ABC):
    """Abstract base for embedding providers."""

    @abstractmethod
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts."""

    @abstractmethod
    def embed_query(self, text: str) -> List[float]:
        """Generate embedding for a single query text."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Return the dimensionality of embeddings produced."""


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI embedding provider using text-embedding models."""

    def __init__(
        self,
        model: str = "text-embedding-3-large",
        api_key: Optional[str] = None,
        batch_size: int = 512,
    ) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "openai package is required for OpenAIEmbeddingProvider. "
                "Install with: pip install openai"
            ) from exc

        self.model = model
        self.batch_size = batch_size
        self.client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))
        self._dimension = 3072 if "large" in model else 1536

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        embeddings = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            response = self.client.embeddings.create(input=batch, model=self.model)
            embeddings.extend([item.embedding for item in response.data])
        return embeddings

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def embed_query(self, text: str) -> List[float]:
        response = self.client.embeddings.create(input=[text], model=self.model)
        return response.data[0].embedding

    @property
    def dimension(self) -> int:
        return self._dimension


class SentenceTransformerProvider(EmbeddingProvider):
    """Local embedding provider using sentence-transformers."""

    def __init__(
        self,
        model_name: str = "sentence-transformers/all-mpnet-base-v2",
        device: Optional[str] = None,
    ) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers is required for SentenceTransformerProvider. "
                "Install with: pip install sentence-transformers"
            ) from exc

        self.model = SentenceTransformer(model_name, device=device)
        self._dimension = self.model.get_sentence_embedding_dimension()

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        embeddings = self.model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
        return embeddings.tolist()

    def embed_query(self, text: str) -> List[float]:
        embedding = self.model.encode([text], convert_to_numpy=True, show_progress_bar=False)
        return embedding[0].tolist()

    @property
    def dimension(self) -> int:
        return self._dimension


class EmbeddingService:
    """Facade for embedding generation with provider abstraction."""

    def __init__(self, provider: Optional[EmbeddingProvider] = None) -> None:
        self.provider = provider or self._default_provider()

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        return self.provider.embed_documents(texts)

    def embed_query(self, text: str) -> List[float]:
        return self.provider.embed_query(text)

    @property
    def dimension(self) -> int:
        return self.provider.dimension

    def _default_provider(self) -> EmbeddingProvider:
        """Select provider based on environment configuration."""
        provider_name = os.getenv("EMBEDDING_PROVIDER", "openai").lower()
        if provider_name == "openai":
            model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-large")
            return OpenAIEmbeddingProvider(model=model)
        elif provider_name == "sentence-transformers":
            model = os.getenv("SENTENCE_TRANSFORMERS_MODEL", "sentence-transformers/all-mpnet-base-v2")
            return SentenceTransformerProvider(model_name=model)
        else:
            raise ValueError(f"Unknown embedding provider: {provider_name}")
