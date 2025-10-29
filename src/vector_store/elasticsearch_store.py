"""Elasticsearch vector store integration."""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from elasticsearch import Elasticsearch

from ..embeddings.service import EmbeddingService
from ..ingestion.types import DocumentLike


class ElasticsearchVectorStore:
    """Wrapper for Elasticsearch with vector search capabilities."""

    def __init__(
        self,
        index_name: str,
        embedding_service: EmbeddingService,
        es_url: Optional[str] = None,
        es_username: Optional[str] = None,
        es_password: Optional[str] = None,
    ) -> None:
        self.index_name = index_name
        self.embedding_service = embedding_service
        self.client = self._create_client(
            es_url or os.getenv("ELASTICSEARCH_URL", "http://localhost:9200"),
            es_username or os.getenv("ELASTICSEARCH_USERNAME", "elastic"),
            es_password or os.getenv("ELASTICSEARCH_PASSWORD", os.getenv("ELASTIC_PASSWORD", "changeme")),
        )

    def _create_client(self, url: str, username: str, password: str) -> Elasticsearch:
        return Elasticsearch(
            hosts=[url],
            basic_auth=(username, password),
            verify_certs=False,
        )

    def add_documents(
        self,
        documents: List[DocumentLike],
        *,
        embeddings: Optional[List[List[float]]] = None,
    ) -> List[str]:
        """Index documents with embeddings into Elasticsearch."""
        if not documents:
            return []

        if embeddings is None:
            texts = [doc.page_content for doc in documents]
            embeddings = self.embedding_service.embed_documents(texts)

        doc_ids = []
        for doc, embedding in zip(documents, embeddings):
            doc_id = str(uuid.uuid4())
            body = {
                "id": doc_id,
                "content": doc.page_content,
                "metadata": doc.metadata,
                "embedding": embedding,
                "created_at": datetime.utcnow().isoformat(),
                "source": doc.metadata.get("source", "unknown"),
            }
            self.client.index(index=self.index_name, id=doc_id, document=body)
            doc_ids.append(doc_id)

        # Refresh index to make documents searchable immediately
        self.client.indices.refresh(index=self.index_name)
        return doc_ids

    def similarity_search(
        self,
        query: str,
        k: int = 4,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Perform kNN vector similarity search."""
        query_embedding = self.embedding_service.embed_query(query)

        knn_query = {
            "field": "embedding",
            "query_vector": query_embedding,
            "k": k,
            "num_candidates": k * 10,
        }

        if filters:
            knn_query["filter"] = self._build_filter(filters)

        search_body = {
            "knn": knn_query,
            "_source": ["id", "content", "metadata", "source", "created_at"],
        }

        response = self.client.search(index=self.index_name, body=search_body)
        results = []
        for hit in response["hits"]["hits"]:
            results.append(
                {
                    "id": hit["_source"]["id"],
                    "content": hit["_source"]["content"],
                    "metadata": hit["_source"]["metadata"],
                    "source": hit["_source"]["source"],
                    "score": hit["_score"],
                }
            )
        return results

    def _build_filter(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        """Convert simple key-value filters to Elasticsearch query DSL."""
        must_clauses = []
        for key, value in filters.items():
            must_clauses.append({"term": {f"metadata.{key}": value}})
        return {"bool": {"must": must_clauses}} if must_clauses else {}

    def health_check(self) -> Dict[str, Any]:
        """Check Elasticsearch cluster health."""
        try:
            health = self.client.cluster.health()
            index_exists = self.client.indices.exists(index=self.index_name)
            return {
                "status": health["status"],
                "index_exists": index_exists,
                "cluster_name": health["cluster_name"],
            }
        except Exception as exc:
            return {"status": "error", "error": str(exc)}
