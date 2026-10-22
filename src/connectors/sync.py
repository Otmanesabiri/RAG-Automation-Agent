"""Incremental synchronisation of a connector into the index."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from ..observability.logging import get_logger
from .base import SourceConnector

logger = get_logger(__name__)

Progress = Callable[[Dict[str, int]], None]


class SyncStateStore:
    """Remembers the indexed version of each document, per connector (Redis or memory)."""

    def __init__(self, redis_client=None, prefix: str = "rag:sync:") -> None:
        self.redis = redis_client
        self.prefix = prefix
        self._memory: Dict[str, Dict[str, str]] = {}

    def load(self, connector: str) -> Dict[str, str]:
        if self.redis is None:
            return dict(self._memory.get(connector, {}))
        raw = self.redis.hgetall(self.prefix + connector)
        return {_text(k): _text(v) for k, v in raw.items()}

    def save(self, connector: str, versions: Dict[str, str]) -> None:
        if self.redis is None:
            self._memory[connector] = dict(versions)
            return
        key = self.prefix + connector
        pipe = self.redis.pipeline()
        pipe.delete(key)
        if versions:
            pipe.hset(key, mapping=versions)
        pipe.execute()


def _text(value) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else value


@dataclass
class SyncReport:
    connector: str
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0
    failed: int = 0
    errors: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, object]:
        return dict(self.__dict__)


class SyncService:
    """Index new and changed documents, remove the ones that disappeared from the source."""

    def __init__(self, pipeline, vector_store, state: SyncStateStore) -> None:
        self.pipeline = pipeline
        self.vector_store = vector_store
        self.state = state

    def sync(self, connector: SourceConnector, *, full: bool = False, progress: Optional[Progress] = None) -> SyncReport:
        report = SyncReport(connector=connector.name)
        known = {} if full else self.state.load(connector.name)
        current: Dict[str, str] = {}

        for item in connector.list_items():
            previous = known.get(item.source)
            if previous == item.version:
                current[item.source] = item.version
                report.unchanged += 1
                continue
            try:
                metadata = {**connector.metadata, **item.metadata, "connector": connector.name, "source": item.source}
                result = self.pipeline.ingest_bytes(content=connector.fetch(item), filename=item.filename, metadata=metadata)
                self.vector_store.add_documents(result.chunks)
                current[item.source] = item.version
                if previous is None:
                    report.added += 1
                else:
                    report.updated += 1
            except Exception as exc:  # one bad file must not stop the sync
                report.failed += 1
                report.errors.append(f"{item.source}: {type(exc).__name__}: {exc}")
                if previous is not None:
                    current[item.source] = previous  # keep the old version indexed, retry next time
                logger.warning(f"Sync {connector.name}: failed to index {item.source}: {exc}")
            if progress:
                progress(report.as_dict())

        for source in set(known) - set(current):
            self.vector_store.delete_document(self.vector_store.document_id_for(source))
            report.deleted += 1

        self.state.save(connector.name, current)
        logger.info(f"Sync {connector.name}: {json.dumps(report.as_dict())}")
        return report
