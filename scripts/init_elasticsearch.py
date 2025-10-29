"""Bootstrap Elasticsearch indices for the RAG Automation Agent."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv
from elasticsearch import Elasticsearch
from elasticsearch.exceptions import NotFoundError

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "elasticsearch_index.json"


def load_index_config(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:  # pragma: no cover
        raise SystemExit(f"Index configuration not found at {path}") from exc


def create_client(url: str, username: str | None, password: str | None) -> Elasticsearch:
    return Elasticsearch(
        hosts=[url],
        basic_auth=(username or "elastic", password or os.environ.get("ELASTIC_PASSWORD", "changeme")),
        verify_certs=False,
    )


def ensure_index(client: Elasticsearch, index_name: str, body: Dict[str, Any]) -> None:
    exists = client.indices.exists(index=index_name)
    if exists:
        print(f"[skip] Index '{index_name}' already exists")
        return

    client.indices.create(index=index_name, **body)
    print(f"[create] Index '{index_name}' created successfully")


def delete_index(client: Elasticsearch, index_name: str) -> None:
    try:
        client.indices.delete(index=index_name)
        print(f"[delete] Index '{index_name}' removed")
    except NotFoundError:
        print(f"[skip] Index '{index_name}' does not exist")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize Elasticsearch indices for the RAG Automation Agent")
    parser.add_argument("--index", default=os.getenv("ELASTICSEARCH_INDEX", "rag_documents"), help="Index name to create")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to index configuration JSON")
    parser.add_argument("--url", default=os.getenv("ELASTICSEARCH_URL", "http://localhost:9200"), help="Elasticsearch endpoint URL")
    parser.add_argument("--username", default=os.getenv("ELASTICSEARCH_USERNAME", "elastic"), help="Elasticsearch username")
    parser.add_argument("--password", default=os.getenv("ELASTICSEARCH_PASSWORD", os.getenv("ELASTIC_PASSWORD", "changeme")), help="Elasticsearch password")
    parser.add_argument("--delete", action="store_true", help="Delete the index instead of creating it")
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()

    config_path = Path(args.config)
    index_body = load_index_config(config_path)
    client = create_client(args.url, args.username, args.password)

    if args.delete:
        delete_index(client, args.index)
    else:
        ensure_index(client, args.index, index_body)


if __name__ == "__main__":
    main()
