"""Bootstrap Elasticsearch indices for the RAG Automation Agent.

The index is sized for the configured embedding model (EMBEDDING_PROVIDER and
its model variable), exactly as the API does on startup.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from elasticsearch.exceptions import NotFoundError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.embeddings.service import EmbeddingService  # noqa: E402
from src.vector_store.client import create_es_client  # noqa: E402
from src.vector_store.elasticsearch_store import ElasticsearchVectorStore  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize Elasticsearch indices for the RAG Automation Agent")
    parser.add_argument("--index", default=os.getenv("ELASTICSEARCH_INDEX", "rag_documents"), help="Index name to create")
    parser.add_argument("--url", default=os.getenv("ELASTICSEARCH_URL", "http://localhost:9200"), help="Elasticsearch endpoint URL")
    parser.add_argument("--username", default=os.getenv("ELASTICSEARCH_USERNAME", "elastic"), help="Elasticsearch username")
    parser.add_argument("--password", default=os.getenv("ELASTICSEARCH_PASSWORD", os.getenv("ELASTIC_PASSWORD")), help="Elasticsearch password")
    parser.add_argument("--delete", action="store_true", help="Delete the index instead of creating it")
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()
    client = create_es_client(args.url, args.username, args.password)

    if args.delete:
        try:
            client.indices.delete(index=args.index)
            print(f"[delete] Index '{args.index}' removed")
        except NotFoundError:
            print(f"[skip] Index '{args.index}' does not exist")
        return

    embedding_service = EmbeddingService()
    store = ElasticsearchVectorStore(args.index, embedding_service, client=client)
    if store.ensure_index():
        print(f"[create] Index '{args.index}' created ({embedding_service.dimension} dims, {embedding_service.model_name})")
    else:
        print(f"[skip] Index '{args.index}' already exists and matches {embedding_service.dimension} dims")


if __name__ == "__main__":
    main()
