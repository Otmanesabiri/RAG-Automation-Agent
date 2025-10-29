"""Configuration models for ingestion services."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional


@dataclass(slots=True)
class LoaderConfig:
    """Configuration for document loaders.

    Attributes:
        allowed_mime_types: Optional whitelist of MIME types allowed for ingestion.
        temp_dir: Directory used to stage downloaded or extracted files.
        use_unstructured: Flag to enable Unstructured loaders for exotic formats.
    """

    allowed_mime_types: Optional[Iterable[str]] = None
    temp_dir: Path = Path("/tmp/rag_agent")
    use_unstructured: bool = True


@dataclass(slots=True)
class ChunkingConfig:
    """Settings controlling text splitting behaviour."""

    chunk_size: int = 800
    chunk_overlap: int = 120
    separators: Optional[List[str]] = None
    metadata_fields: Dict[str, str] = field(default_factory=dict)
    language: Optional[str] = None

    def __post_init__(self) -> None:
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        if self.chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if self.chunk_overlap < 0:
            raise ValueError("chunk_overlap must be non-negative")
