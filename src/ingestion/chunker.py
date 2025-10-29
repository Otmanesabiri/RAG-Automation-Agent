"""Chunking utilities for document preprocessing."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, List, Optional

from langchain_text_splitters import RecursiveCharacterTextSplitter

from .config import ChunkingConfig
from .types import DocumentLike


class ChunkingService:
    """Apply configurable chunking strategies to LangChain documents."""
 
    def __init__(self, config: Optional[ChunkingConfig] = None, *, enable_heuristics: bool = True) -> None:
        self.config = config or ChunkingConfig()
        self.enable_heuristics = enable_heuristics
 
    def split_documents(self, documents: Iterable[DocumentLike]) -> List[DocumentLike]:
        """Split documents into smaller chunks with metadata propagation."""

        chunks: List[DocumentLike] = []
        for document in documents:
            config = self._resolve_config(document)
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=config.chunk_size,
                chunk_overlap=config.chunk_overlap,
                separators=config.separators,
            )
            split_docs = splitter.split_documents([document])
            for idx, chunk in enumerate(split_docs):
                chunk.metadata = {
                    **document.metadata,
                    **config.metadata_fields,
                    "chunk_index": idx,
                    "chunk_count": len(split_docs),
                    "chunk_size": config.chunk_size,
                    "chunk_overlap": config.chunk_overlap,
                    "language": config.language or document.metadata.get("language"),
                    "split_timestamp": datetime.utcnow().isoformat(),
                }
            chunks.extend(split_docs)
        return chunks
 
    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _resolve_config(self, document: DocumentLike) -> ChunkingConfig:
        if not self.enable_heuristics:
            return self.config
 
        doc_length = len(document.page_content)
        metadata = document.metadata or {}
        dynamic_config = ChunkingConfig(
            chunk_size=self.config.chunk_size,
            chunk_overlap=self.config.chunk_overlap,
            separators=self.config.separators,
            metadata_fields={**self.config.metadata_fields},
            language=self.config.language or metadata.get("language"),
        )
 
        # Adjust chunk size based on simple heuristics
        page_count = metadata.get("page_count") or metadata.get("num_pages")
        if isinstance(page_count, int) and page_count > 50:
            dynamic_config.chunk_size = min(max(dynamic_config.chunk_size + 200, 800), 1200)
        elif isinstance(page_count, int) and page_count < 5:
            dynamic_config.chunk_size = max(dynamic_config.chunk_size - 300, 400)
        elif doc_length < 2000:
            dynamic_config.chunk_size = max(dynamic_config.chunk_size - 200, 400)
        elif doc_length > 12000:
            dynamic_config.chunk_size = min(dynamic_config.chunk_size + 200, 1200)
 
        # Ensure overlap scales proportionally (but keeps gap >= 0)
        dynamic_config.chunk_overlap = max(int(dynamic_config.chunk_size * 0.15), 50)
        if dynamic_config.chunk_overlap >= dynamic_config.chunk_size:
            dynamic_config.chunk_overlap = dynamic_config.chunk_size // 4
 
        return dynamic_config
