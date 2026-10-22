"""External document sources, incremental sync and background jobs."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict

from .base import SourceConnector, SourceItem
from .jobs import JobManager
from .sources import ConfluenceConnector, LocalFolderConnector, S3Connector, build_connector
from .sync import SyncReport, SyncService, SyncStateStore


def load_connectors(path: str | None = None) -> Dict[str, SourceConnector]:
    """Connectors declared in the JSON file at CONNECTORS_CONFIG (none when unset).

    Format: {"<name>": {"type": "local" | "s3" | "confluence", ...type options}}
    """
    path = path or os.getenv("CONNECTORS_CONFIG")
    if not path:
        return {}
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    return {name: build_connector(name, options) for name, options in config.items()}


__all__ = [
    "ConfluenceConnector",
    "JobManager",
    "LocalFolderConnector",
    "S3Connector",
    "SourceConnector",
    "SourceItem",
    "SyncReport",
    "SyncService",
    "SyncStateStore",
    "build_connector",
    "load_connectors",
]
