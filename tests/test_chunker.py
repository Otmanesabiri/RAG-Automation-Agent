"""Unit tests for chunking heuristics."""

import unittest
from dataclasses import dataclass, field
from typing import Any, Dict
@dataclass
class _DocumentStub:
    page_content: str
    metadata: Dict[str, Any] = field(default_factory=dict)


from src.ingestion.chunker import ChunkingService, ChunkingConfig


class ChunkingServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.config = ChunkingConfig(chunk_size=800, chunk_overlap=120)
        self.service = ChunkingService(self.config)

    def test_small_document_uses_smaller_chunks(self) -> None:
        document = _DocumentStub(page_content="Lorem ipsum" * 50, metadata={"page_count": 2})
        resolved = self.service._resolve_config(document)

        self.assertLessEqual(resolved.chunk_size, 800)
        self.assertLess(resolved.chunk_overlap, resolved.chunk_size)

    def test_large_document_increases_chunk_size(self) -> None:
        document = _DocumentStub(page_content="Lorem ipsum" * 5000, metadata={"page_count": 80})
        resolved = self.service._resolve_config(document)

        self.assertGreaterEqual(resolved.chunk_size, 800)
        self.assertLess(resolved.chunk_overlap, resolved.chunk_size)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
