"""High-level ingestion pipeline orchestrating loading and chunking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from .chunker import ChunkingService
from .config import ChunkingConfig, LoaderConfig
from .loaders import DocumentLoaderFactory, LoadedDocumentBatch
from .types import DocumentLike


@dataclass(slots=True)
class IngestionResult:
    """Container for processed documents ready for embedding."""

    source: str
    mime_type: Optional[str]
    raw_documents: List[DocumentLike]
    chunks: List[DocumentLike]

    def total_tokens(self) -> int:
        return sum(len(doc.page_content) for doc in self.chunks)


class IngestionPipeline:
    """Pipeline that couples document loading and chunking."""

    def __init__(
        self,
        loader_config: Optional[LoaderConfig] = None,
        chunking_config: Optional[ChunkingConfig] = None,
    ) -> None:
        self.loader_factory = DocumentLoaderFactory(loader_config)
        self.chunking_service = ChunkingService(chunking_config)

    def ingest_path(
        self,
        path: str,
        *,
        metadata: Optional[Dict[str, str]] = None,
    ) -> IngestionResult:
        batch = self.loader_factory.load_from_path(path, metadata=metadata)
        chunks = self._chunk(batch.documents)
        return self._build_result(batch, chunks)

    def ingest_url(
        self,
        url: str,
        *,
        metadata: Optional[Dict[str, str]] = None,
    ) -> IngestionResult:
        batch = self.loader_factory.load_from_url(url, metadata=metadata)
        chunks = self._chunk(batch.documents)
        return self._build_result(batch, chunks)

    def ingest_bytes(
        self,
        *,
        content: bytes,
        filename: str,
        metadata: Optional[Dict[str, str]] = None,
    ) -> IngestionResult:
        batch = self.loader_factory.load_from_bytes(content=content, filename=filename, metadata=metadata)
        chunks = self._chunk(batch.documents)
        return self._build_result(batch, chunks)

    def _chunk(self, documents: Iterable[DocumentLike]) -> List[DocumentLike]:
        return self.chunking_service.split_documents(documents)

    def _build_result(
        self,
        batch: LoadedDocumentBatch,
        chunks: List[DocumentLike],
    ) -> IngestionResult:
        return IngestionResult(
            source=batch.source,
            mime_type=batch.mime_type,
            raw_documents=list(batch.documents),
            chunks=chunks,
        )
