"""Elasticsearch vector store integration."""

from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from elasticsearch import Elasticsearch, helpers

from ..embeddings.service import EmbeddingService
from ..ingestion.types import DocumentLike
from .client import create_es_client

INDEX_TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "configs" / "elasticsearch_index.json"


class IndexDimensionMismatchError(RuntimeError):
    """Raised when the index was built for a different embedding model."""


class ElasticsearchVectorStore:
    """Wrapper for Elasticsearch with vector search capabilities."""

    def __init__(
        self,
        index_name: str,
        embedding_service: EmbeddingService,
        es_url: Optional[str] = None,
        es_username: Optional[str] = None,
        es_password: Optional[str] = None,
        *,
        client: Optional[Elasticsearch] = None,
        refresh: Optional[str] = None,
    ) -> None:
        self.index_name = index_name
        self.embedding_service = embedding_service
        self.client = client or create_es_client(es_url, es_username, es_password)
        # "wait_for" makes new chunks searchable before the request returns
        # without forcing an expensive refresh; "false" for bulk backfills.
        self.refresh = refresh or os.getenv("INDEX_REFRESH", "wait_for")

    def ensure_index(self) -> bool:
        """Create the index if missing, sized for the configured embedding model.

        Returns True when the index was created. Raises
        :class:`IndexDimensionMismatchError` when an existing index was built
        for vectors of another size (i.e. another embedding model).
        """
        dims = self.embedding_service.dimension
        model = self.embedding_service.model_name
        if self.client.indices.exists(index=self.index_name):
            mapping = self.client.indices.get_mapping(index=self.index_name)
            properties = mapping[self.index_name]["mappings"].get("properties", {})
            existing = properties.get("embedding", {}).get("dims")
            if existing is not None and existing != dims:
                raise IndexDimensionMismatchError(
                    f"Index '{self.index_name}' stores {existing}-dim vectors but the embedding model "
                    f"'{model}' produces {dims}. Use another ELASTICSEARCH_INDEX or re-create the index."
                )
            return False

        body = json.loads(INDEX_TEMPLATE_PATH.read_text(encoding="utf-8"))
        body["mappings"]["properties"]["embedding"]["dims"] = dims
        body["mappings"]["_meta"] = {"embedding_model": model, "embedding_dims": dims}
        self.client.indices.create(index=self.index_name, **body)
        return True

    @staticmethod
    def document_id_for(source: str) -> str:
        """Stable id of a source document: re-ingesting the same source replaces it."""
        return hashlib.sha256(source.encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def chunk_id_for(document_id: str, index: int, content: str) -> str:
        digest = hashlib.sha256(f"{document_id}:{index}:{content}".encode("utf-8")).hexdigest()
        return digest[:32]

    def add_documents(
        self,
        documents: List[DocumentLike],
        *,
        embeddings: Optional[List[List[float]]] = None,
    ) -> List[str]:
        """Index chunks with embeddings, replacing earlier versions of the same sources.

        Chunk ids are derived from the source, chunk position and content, so
        re-ingesting identical content overwrites instead of duplicating, and
        chunks that no longer exist in the new version are deleted.
        """
        if not documents:
            return []

        if embeddings is None:
            texts = [doc.page_content for doc in documents]
            embeddings = self.embedding_service.embed_documents(texts)

        now = datetime.now(timezone.utc).isoformat()
        actions = []
        ids_by_document: Dict[str, List[str]] = defaultdict(list)
        for position, (doc, embedding) in enumerate(zip(documents, embeddings)):
            source = doc.metadata.get("source", "unknown")
            document_id = self.document_id_for(source)
            chunk_index = doc.metadata.get("chunk_index", position)
            chunk_id = self.chunk_id_for(document_id, chunk_index, doc.page_content)
            ids_by_document[document_id].append(chunk_id)
            actions.append(
                {
                    "_index": self.index_name,
                    "_id": chunk_id,
                    "_source": {
                        "id": chunk_id,
                        "document_id": document_id,
                        "content": doc.page_content,
                        "metadata": doc.metadata,
                        "embedding": embedding,
                        "created_at": now,
                        "source": source,
                    },
                }
            )

        helpers.bulk(self.client, actions, refresh=self.refresh)
        for document_id, chunk_ids in ids_by_document.items():
            self._delete_stale_chunks(document_id, keep=chunk_ids)
        return [action["_id"] for action in actions]

    def _delete_stale_chunks(self, document_id: str, keep: List[str]) -> None:
        self.client.delete_by_query(
            index=self.index_name,
            query={"bool": {"filter": [{"term": {"document_id": document_id}}], "must_not": [{"ids": {"values": keep}}]}},
            refresh=True,
            conflicts="proceed",
        )

    def list_documents(self, *, size: int = 20, after: Optional[str] = None) -> Dict[str, Any]:
        """Page through source documents (one entry per document, not per chunk)."""
        composite: Dict[str, Any] = {"size": size, "sources": [{"document_id": {"terms": {"field": "document_id"}}}]}
        if after:
            composite["after"] = {"document_id": after}
        response = self.client.search(
            index=self.index_name,
            size=0,
            aggs={
                "documents": {
                    "composite": composite,
                    "aggs": {
                        "sample": {"top_hits": {"size": 1, "_source": ["source", "created_at", "metadata.mime_type"]}},
                        "last_indexed": {"max": {"field": "created_at"}},
                    },
                }
            },
        )
        agg = response["aggregations"]["documents"]
        documents = []
        for bucket in agg["buckets"]:
            sample = bucket["sample"]["hits"]["hits"][0]["_source"]
            documents.append(
                {
                    "document_id": bucket["key"]["document_id"],
                    "source": sample.get("source"),
                    "mime_type": (sample.get("metadata") or {}).get("mime_type"),
                    "chunks": bucket["doc_count"],
                    "indexed_at": bucket["last_indexed"].get("value_as_string") or sample.get("created_at"),
                }
            )
        next_after = agg.get("after_key", {}).get("document_id") if len(documents) == size else None
        return {"documents": documents, "next": next_after}

    def get_document(self, document_id: str) -> Optional[Dict[str, Any]]:
        """Return a document with its chunks in order, or None if unknown."""
        response = self.client.search(
            index=self.index_name,
            query={"term": {"document_id": document_id}},
            size=10000,
            _source=["id", "content", "metadata", "source", "created_at"],
        )
        hits = [hit["_source"] for hit in response["hits"]["hits"]]
        if not hits:
            return None
        hits.sort(key=lambda h: (h.get("metadata") or {}).get("chunk_index", 0))
        return {
            "document_id": document_id,
            "source": hits[0].get("source"),
            "chunks": [
                {"id": h["id"], "content": h["content"], "chunk_index": (h.get("metadata") or {}).get("chunk_index")}
                for h in hits
            ],
        }

    def delete_document(self, document_id: str) -> int:
        """Delete every chunk of a document; returns the number of chunks removed."""
        response = self.client.delete_by_query(
            index=self.index_name,
            query={"term": {"document_id": document_id}},
            refresh=True,
            conflicts="proceed",
        )
        return response.get("deleted", 0)

    def similarity_search(
        self,
        query: str,
        k: int = 4,
        filters: Optional[Dict[str, Any]] = None,
        min_score: Optional[float] = None,
        max_age_days: Optional[int] = None,
        user_permissions: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Perform kNN vector similarity search with advanced filtering.
        
        Args:
            query: Search query text
            k: Number of results to return
            filters: Custom metadata filters (e.g., {"category": "education"})
            min_score: Minimum similarity score threshold (0-1)
            max_age_days: Maximum age of documents in days
            user_permissions: List of permission levels user has access to
        
        Returns:
            List of matching documents with scores
        """
        query_embedding = self.embedding_service.embed_query(query)

        knn_query = {
            "field": "embedding",
            "query_vector": query_embedding,
            "k": k,
            "num_candidates": k * 10,
        }

        # Build combined filters
        combined_filters = self._build_advanced_filters(
            filters=filters,
            max_age_days=max_age_days,
            user_permissions=user_permissions
        )
        
        if combined_filters:
            knn_query["filter"] = combined_filters

        search_body = {
            "knn": knn_query,
            "_source": ["id", "document_id", "content", "metadata", "source", "created_at"],
        }
        
        # Add minimum score if specified
        if min_score is not None:
            search_body["min_score"] = min_score

        response = self.client.search(index=self.index_name, body=search_body)
        results = []
        for hit in response["hits"]["hits"]:
            results.append(
                {
                    "id": hit["_source"]["id"],
                    "document_id": hit["_source"].get("document_id"),
                    "content": hit["_source"]["content"],
                    "metadata": hit["_source"]["metadata"],
                    "source": hit["_source"]["source"],
                    "score": hit["_score"],
                    "created_at": hit["_source"].get("created_at"),
                }
            )
        return results

    def _build_filter(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        """Convert simple key-value filters to Elasticsearch query DSL."""
        must_clauses = []
        for key, value in filters.items():
            must_clauses.append({"term": {f"metadata.{key}": value}})
        return {"bool": {"must": must_clauses}} if must_clauses else {}
    
    def _build_advanced_filters(
        self,
        filters: Optional[Dict[str, Any]] = None,
        max_age_days: Optional[int] = None,
        user_permissions: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Build advanced Elasticsearch filters.
        
        Args:
            filters: Custom metadata filters
            max_age_days: Maximum document age in days
            user_permissions: List of access levels user has
        
        Returns:
            Elasticsearch query DSL filter or None
        """
        must_clauses = []
        
        # Add custom metadata filters
        if filters:
            for key, value in filters.items():
                if isinstance(value, list):
                    # Multiple values = OR condition
                    must_clauses.append({"terms": {f"metadata.{key}": value}})
                else:
                    # Single value = exact match
                    must_clauses.append({"term": {f"metadata.{key}": value}})
        
        # Add age filter (documents newer than max_age_days)
        if max_age_days is not None:
            from datetime import datetime, timedelta
            cutoff_date = (datetime.utcnow() - timedelta(days=max_age_days)).isoformat()
            must_clauses.append({
                "range": {
                    "created_at": {
                        "gte": cutoff_date
                    }
                }
            })
        
        # Add permission filter (user can only see docs with their access levels)
        if user_permissions is not None:
            # If metadata.access_level exists, it must be in user_permissions
            # If metadata.access_level doesn't exist, document is public (accessible to all)
            must_clauses.append({
                "bool": {
                    "should": [
                        # Document has no access_level = public
                        {"bool": {"must_not": {"exists": {"field": "metadata.access_level"}}}},
                        # Document access_level matches user permissions
                        {"terms": {"metadata.access_level": user_permissions}}
                    ],
                    "minimum_should_match": 1
                }
            })
        
        # Return combined filter
        if must_clauses:
            return {"bool": {"must": must_clauses}}
        return None

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
