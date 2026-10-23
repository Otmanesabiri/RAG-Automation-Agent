"""Built-in connectors: local folder, Amazon S3, Confluence."""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests

from .base import SourceConnector, SourceItem

DEFAULT_PATTERNS = ["*.pdf", "*.docx", "*.txt", "*.md", "*.html", "*.htm"]


class LocalFolderConnector(SourceConnector):
    """Files under a server-side folder (recursive), matched by glob patterns."""

    def __init__(self, name: str, path: str, patterns: Optional[List[str]] = None, metadata=None) -> None:
        super().__init__(name, metadata)
        self.root = Path(path).expanduser().resolve()
        self.patterns = patterns or DEFAULT_PATTERNS

    def list_items(self) -> Iterable[SourceItem]:
        if not self.root.is_dir():
            raise FileNotFoundError(f"Connector '{self.name}': folder not found: {self.root}")
        for path in sorted(self.root.rglob("*")):
            if path.is_file() and any(fnmatch.fnmatch(path.name.lower(), p.lower()) for p in self.patterns):
                relative = path.relative_to(self.root).as_posix()
                stat = path.stat()
                yield SourceItem(
                    source=f"{self.name}:{relative}",
                    version=f"{stat.st_mtime_ns}-{stat.st_size}",
                    filename=path.name,
                    metadata={"path": relative},
                )

    def fetch(self, item: SourceItem) -> bytes:
        path = (self.root / item.metadata["path"]).resolve()
        if self.root not in path.parents:  # never read outside the configured folder
            raise PermissionError(f"Path escapes connector root: {item.metadata['path']}")
        return path.read_bytes()


class S3Connector(SourceConnector):
    """Objects of an S3 bucket/prefix (needs boto3; credentials from the usual AWS chain)."""

    def __init__(self, name: str, bucket: str, prefix: str = "", patterns=None, metadata=None, client=None) -> None:
        super().__init__(name, metadata)
        self.bucket = bucket
        self.prefix = prefix
        self.patterns = patterns or DEFAULT_PATTERNS
        if client is None:
            import boto3

            client = boto3.client("s3")
        self.client = client

    def list_items(self) -> Iterable[SourceItem]:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                filename = key.rsplit("/", 1)[-1]
                if filename and any(fnmatch.fnmatch(filename.lower(), p.lower()) for p in self.patterns):
                    yield SourceItem(
                        source=f"s3://{self.bucket}/{key}",
                        version=obj.get("ETag", "").strip('"') or str(obj.get("LastModified")),
                        filename=filename,
                        metadata={"key": key},
                    )

    def fetch(self, item: SourceItem) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=item.metadata["key"])["Body"].read()


class ConfluenceConnector(SourceConnector):
    """Pages of a Confluence space (REST API, basic auth with an API token)."""

    PAGE_SIZE = 50

    def __init__(
        self,
        name: str,
        base_url: str,
        space_key: str,
        username: str,
        token_env: str = "CONFLUENCE_API_TOKEN",
        metadata=None,
        session: Optional[requests.Session] = None,
    ) -> None:
        super().__init__(name, metadata)
        self.base_url = base_url.rstrip("/")
        self.space_key = space_key
        self.session = session or requests.Session()
        token = os.getenv(token_env)
        if token:
            self.session.auth = (username, token)

    def list_items(self) -> Iterable[SourceItem]:
        start = 0
        while True:
            response = self.session.get(
                f"{self.base_url}/rest/api/content",
                params={
                    "spaceKey": self.space_key,
                    "type": "page",
                    "expand": "version",
                    "start": start,
                    "limit": self.PAGE_SIZE,
                },
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            for page in data.get("results", []):
                yield SourceItem(
                    source=f"{self.base_url}/pages/{page['id']}",
                    version=str(page.get("version", {}).get("number", "")),
                    filename=f"{page.get('title', page['id'])}.html",
                    metadata={"page_id": page["id"], "title": page.get("title")},
                )
            if data.get("size", 0) < self.PAGE_SIZE:
                return
            start += self.PAGE_SIZE

    def fetch(self, item: SourceItem) -> bytes:
        response = self.session.get(
            f"{self.base_url}/rest/api/content/{item.metadata['page_id']}",
            params={"expand": "body.storage"},
            timeout=30,
        )
        response.raise_for_status()
        page = response.json()
        html = page.get("body", {}).get("storage", {}).get("value", "")
        return f"<html><body><h1>{page.get('title', '')}</h1>{html}</body></html>".encode("utf-8")


CONNECTOR_TYPES = {"local": LocalFolderConnector, "s3": S3Connector, "confluence": ConfluenceConnector}


def build_connector(name: str, config: Dict[str, Any]) -> SourceConnector:
    options = dict(config)
    kind = options.pop("type", None)
    if kind not in CONNECTOR_TYPES:
        raise ValueError(f"Connector '{name}': unknown type {kind!r} (expected one of {sorted(CONNECTOR_TYPES)})")
    return CONNECTOR_TYPES[kind](name=name, **options)
