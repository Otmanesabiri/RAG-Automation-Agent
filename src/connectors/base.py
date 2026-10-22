"""Source connector interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable


@dataclass(frozen=True)
class SourceItem:
    """One document in an external source.

    ``source`` identifies the document across syncs (it becomes the document's
    source in the index); ``version`` changes whenever its content changes
    (mtime, ETag, page version...).
    """

    source: str
    version: str
    filename: str
    metadata: Dict[str, Any] = field(default_factory=dict, compare=False, hash=False)


class SourceConnector(ABC):
    """Lists the documents of a source and downloads them."""

    def __init__(self, name: str, metadata: Dict[str, Any] | None = None) -> None:
        self.name = name
        self.metadata = metadata or {}

    @abstractmethod
    def list_items(self) -> Iterable[SourceItem]:
        """Every document currently in the source."""

    @abstractmethod
    def fetch(self, item: SourceItem) -> bytes:
        """Raw content of one document."""
