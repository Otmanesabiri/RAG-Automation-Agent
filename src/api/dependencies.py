"""Dependency helpers for the Flask application."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from flask import Flask, current_app

from ..embeddings.service import EmbeddingService
from ..ingestion.config import ChunkingConfig, LoaderConfig
from ..ingestion.pipeline import IngestionPipeline
from ..llm.rag_service import RAGService
from ..llm.service import LLMService
from ..vector_store.elasticsearch_store import ElasticsearchVectorStore

from .state import SessionStore


def register_dependencies(app: Flask) -> None:
    app.extensions["session_store"] = SessionStore()
    # Lazily initialize ingestion pipeline to avoid heavy imports during app startup.
    app.extensions["ingestion_pipeline_factory"] = _build_pipeline
    app.extensions["embedding_service_factory"] = _build_embedding_service
    app.extensions["vector_store_factory"] = _build_vector_store
    app.extensions["llm_service_factory"] = _build_llm_service
    app.extensions["rag_service_factory"] = _build_rag_service


def get_session_store() -> SessionStore:
    store = current_app.extensions.get("session_store")
    if not store:
        store = SessionStore()
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
    return ElasticsearchVectorStore(index_name=index_name, embedding_service=embedding_service)


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


def get_rag_service() -> RAGService:
    factory = current_app.extensions.get("rag_service_factory", _build_rag_service)
    return factory()
