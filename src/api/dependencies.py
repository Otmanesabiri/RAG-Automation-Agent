"""Dependency helpers for the Flask application."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Dict

from flask import Flask, current_app

from ..agent import AgentRunner, build_document_tools
from ..auth import current_principal, search_scope
from ..connectors import JobManager, SourceConnector, SyncService, SyncStateStore, load_connectors
from ..embeddings.service import EmbeddingService
from ..ingestion.config import ChunkingConfig, LoaderConfig
from ..ingestion.pipeline import IngestionPipeline
from ..llm.rag_service import RAGService
from ..llm.service import LLMService
from ..redis_client import get_redis_client
from ..vector_store.elasticsearch_store import ElasticsearchVectorStore
from .feedback import FeedbackStore
from .state import RedisSessionStore, SessionStore, build_session_store


def register_dependencies(app: Flask) -> None:
    app.extensions["session_store"] = build_session_store()
    # Lazily initialize ingestion pipeline to avoid heavy imports during app startup.
    app.extensions["ingestion_pipeline_factory"] = _build_pipeline
    app.extensions["embedding_service_factory"] = _build_embedding_service
    app.extensions["vector_store_factory"] = _build_vector_store
    app.extensions["llm_service_factory"] = _build_llm_service
    app.extensions["rag_service_factory"] = _build_rag_service
    app.extensions["agent_runner_factory"] = _build_agent_runner


def get_session_store() -> SessionStore | RedisSessionStore:
    store = current_app.extensions.get("session_store")
    if not store:
        store = build_session_store()
        current_app.extensions["session_store"] = store
    return store


def get_ingestion_pipeline() -> IngestionPipeline:
    factory = current_app.extensions.get("ingestion_pipeline_factory", _build_pipeline)
    return factory()


@lru_cache(maxsize=1)
def _build_pipeline() -> IngestionPipeline:
    chunk_size = int(os.getenv("INGEST_CHUNK_SIZE", "800"))
    chunk_overlap = int(os.getenv("INGEST_CHUNK_OVERLAP", "120"))
    loader_config = LoaderConfig()
    chunking_config = ChunkingConfig(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    return IngestionPipeline(loader_config=loader_config, chunking_config=chunking_config)


@lru_cache(maxsize=1)
def _build_embedding_service() -> EmbeddingService:
    return EmbeddingService()


@lru_cache(maxsize=1)
def _build_vector_store() -> ElasticsearchVectorStore:
    embedding_service = _build_embedding_service()
    index_name = os.getenv("ELASTICSEARCH_INDEX", "rag_documents")
    store = ElasticsearchVectorStore(index_name=index_name, embedding_service=embedding_service)
    if os.getenv("ELASTICSEARCH_AUTO_CREATE_INDEX", "true").lower() == "true":
        store.ensure_index()
    return store


def get_embedding_service() -> EmbeddingService:
    factory = current_app.extensions.get("embedding_service_factory", _build_embedding_service)
    return factory()


def get_vector_store() -> ElasticsearchVectorStore:
    factory = current_app.extensions.get("vector_store_factory", _build_vector_store)
    return factory()


@lru_cache(maxsize=1)
def _build_llm_service() -> LLMService:
    return LLMService()


@lru_cache(maxsize=1)
def _build_rag_service() -> RAGService:
    vector_store = _build_vector_store()
    llm_service = _build_llm_service()
    return RAGService(vector_store=vector_store, llm_service=llm_service)


def get_llm_service() -> LLMService:
    factory = current_app.extensions.get("llm_service_factory", _build_llm_service)
    return factory()


def get_connectors() -> Dict[str, SourceConnector]:
    factory = current_app.extensions.get("connectors_factory", _build_connectors)
    return factory()


@lru_cache(maxsize=1)
def _build_connectors() -> Dict[str, SourceConnector]:
    return load_connectors()


def get_sync_service() -> SyncService:
    factory = current_app.extensions.get("sync_service_factory", _build_sync_service)
    return factory()


@lru_cache(maxsize=1)
def _build_sync_service() -> SyncService:
    return SyncService(_build_pipeline(), _build_vector_store(), SyncStateStore(get_redis_client()))


def get_feedback_store() -> FeedbackStore:
    store = current_app.extensions.get("feedback_store")
    if store is None:
        store = current_app.extensions["feedback_store"] = FeedbackStore(get_redis_client())
    return store


def get_job_manager() -> JobManager:
    manager = current_app.extensions.get("job_manager")
    if manager is None:
        manager = current_app.extensions["job_manager"] = JobManager(get_redis_client())
    return manager


def get_agent_runner() -> AgentRunner:
    factory = current_app.extensions.get("agent_runner_factory", _build_agent_runner)
    return factory()


@lru_cache(maxsize=1)
def _build_agent_runner() -> AgentRunner:
    llm_service = _build_llm_service()
    registry = build_document_tools(
        _build_rag_service(),
        _build_vector_store(),
        llm_service,
        scope_provider=lambda: search_scope(current_principal()),
    )
    return AgentRunner(llm_service, registry)


def get_rag_service() -> RAGService:
    factory = current_app.extensions.get("rag_service_factory", _build_rag_service)
    return factory()
