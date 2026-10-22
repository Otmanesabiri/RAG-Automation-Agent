"""Deterministic bag-of-words embeddings for tests and smoke evaluations (no model download)."""

from __future__ import annotations

import hashlib
from typing import List


class HashEmbeddings:
    """Each word increments one hashed dimension: texts sharing words get similar vectors."""

    model_name = "hash-bow"

    def __init__(self, dimension: int = 256) -> None:
        self.dimension = dimension

    def _embed(self, text: str) -> List[float]:
        vector = [0.0] * self.dimension
        for word in text.lower().split():
            vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dimension] += 1.0
        return vector if any(vector) else [1.0] + [0.0] * (self.dimension - 1)

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> List[float]:
        return self._embed(text)
