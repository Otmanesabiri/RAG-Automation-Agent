"""Chunking utilities for document preprocessing."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from .config import ChunkingConfig
from .types import DocumentLike

HEADING_LEVELS = [("#", "h1"), ("##", "h2"), ("###", "h3"), ("####", "h4")]
STRUCTURED_MIME_TYPES = {"text/markdown", "text/x-markdown", "text/html", "application/xhtml+xml"}
_MARKDOWN_HEADING = re.compile(r"^#{1,4} \S", re.MULTILINE)


class ChunkingService:
    """Apply configurable chunking strategies to LangChain documents."""

    def __init__(self, config: Optional[ChunkingConfig] = None, *, enable_heuristics: bool = True) -> None:
        self.config = config or ChunkingConfig()
        self.enable_heuristics = enable_heuristics

    def split_documents(
        self,
        documents: Iterable[DocumentLike],
        *,
        config: Optional[ChunkingConfig] = None,
    ) -> List[DocumentLike]:
        """Split documents into smaller chunks with metadata propagation.

        An explicit `config` (e.g. per-request options) is applied as-is,
        bypassing the size heuristics. Markdown and HTML are split on headings
        first (``section`` metadata). ``chunk_index`` is numbered per source, so
        the pages of a PDF form one continuous sequence.
        """

        chunks: List[DocumentLike] = []
        next_index: Dict[str, int] = defaultdict(int)
        for document in documents:
            doc_config = config or self._resolve_config(document)
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=doc_config.chunk_size,
                chunk_overlap=doc_config.chunk_overlap,
                separators=doc_config.separators,
            )
            split_docs = splitter.split_documents(self._sections(document, doc_config))
            source = str(document.metadata.get("source", ""))
            for chunk in split_docs:
                section = " > ".join(chunk.metadata[name] for _, name in HEADING_LEVELS if chunk.metadata.get(name))
                base = {k: v for k, v in chunk.metadata.items() if k not in dict(HEADING_LEVELS).values()}
                chunk.metadata = {
                    **base,
                    **doc_config.metadata_fields,
                    "chunk_index": next_index[source],
                    "chunk_size": doc_config.chunk_size,
                    "chunk_overlap": doc_config.chunk_overlap,
                    "language": doc_config.language or document.metadata.get("language"),
                    "split_timestamp": datetime.now(timezone.utc).isoformat(),
                }
                if section:
                    chunk.metadata["section"] = section
                if isinstance(base.get("page"), int):
                    chunk.metadata["page_number"] = base["page"] + 1  # PyPDF pages are 0-based
                next_index[source] += 1
            chunks.extend(split_docs)

        counts: Dict[str, int] = defaultdict(int)
        for chunk in chunks:
            counts[str(chunk.metadata.get("source", ""))] += 1
        for chunk in chunks:
            chunk.metadata["chunk_count"] = counts[str(chunk.metadata.get("source", ""))]
        return chunks

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _sections(self, document: DocumentLike, config: ChunkingConfig) -> List[DocumentLike]:
        """Split on Markdown headings when the document is structured, else keep it whole."""
        if not config.structure_aware or not self._is_structured(document):
            return [document]
        header_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=HEADING_LEVELS, strip_headers=False)
        sections = header_splitter.split_text(document.page_content)
        for section in sections:
            section.metadata = {**document.metadata, **section.metadata}
        return sections or [document]

    @staticmethod
    def _is_structured(document: DocumentLike) -> bool:
        mime_type = (document.metadata or {}).get("mime_type")
        source = str((document.metadata or {}).get("source", "")).lower()
        if mime_type in STRUCTURED_MIME_TYPES or source.endswith((".md", ".markdown", ".html", ".htm")):
            return bool(_MARKDOWN_HEADING.search(document.page_content))
        return False

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
            structure_aware=self.config.structure_aware,
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
