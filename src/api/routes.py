"""API endpoints for the RAG Automation Agent."""

from __future__ import annotations

import base64
from typing import Any, Dict

from flask import Blueprint, Response, current_app, jsonify, request
from pydantic import ValidationError

from ..auth import rate_limit, require_api_key
from ..observability import get_logger, track_ingestion, track_query
from .dependencies import get_ingestion_pipeline, get_rag_service, get_session_store, get_vector_store
from .schemas import (
    ErrorResponse,
    IngestRequest,
    QueryRequest,
    QueryResponse,
    SessionCreateRequest,
    SessionResponse,
)

api_bp = Blueprint("api", __name__)
logger = get_logger(__name__)


@api_bp.route("/health", methods=["GET"])
def health() -> Response:
    vector_store = get_vector_store()
    es_health = vector_store.health_check()
    status = {
        "status": "ok",
        "version": current_app.config.get("APP_VERSION", "0.1.0"),
        "elasticsearch": es_health.get("status", "unknown"),
    }
    return jsonify(status)


@api_bp.route("/ingest", methods=["POST"])
@rate_limit
@require_api_key
@track_ingestion
def ingest_documents() -> Response:
    payload = _parse_payload(IngestRequest)
    pipeline = get_ingestion_pipeline()
    vector_store = get_vector_store()

    logger.info(f"Ingesting {len(payload.documents)} documents from source={payload.source}")

    created_documents = []
    for document in payload.documents:
        metadata = {**document.metadata}
        if document.mime_type:
            metadata.setdefault("mime_type", document.mime_type)

        if payload.source == "url" and document.url:
            result = pipeline.ingest_url(str(document.url), metadata=metadata)
        else:
            filename = document.filename or "document.txt"
            content_bytes = _resolve_document_bytes(document)
            result = pipeline.ingest_bytes(content=content_bytes, filename=filename, metadata=metadata)

        # Index chunks into Elasticsearch with embeddings
        doc_ids = vector_store.add_documents(result.chunks)
        logger.info(f"Indexed {len(doc_ids)} chunks for source={result.source}")

        created_documents.append(
            {
                "source": result.source,
                "chunks": len(result.chunks),
                "indexed_ids": doc_ids,
                "mime_type": result.mime_type,
            }
        )

    return jsonify({"status": "indexed", "documents": created_documents}), 202


@api_bp.route("/query", methods=["POST"])
@rate_limit
@require_api_key
@track_query
def query_documents() -> Response:
    payload = _parse_payload(QueryRequest)
    session_store = get_session_store()
    rag_service = get_rag_service()
    
    session_id = payload.session_id or session_store.create_session(user_id=None)
    session_store.append_message(session_id, "user", payload.query)

    logger.info(f"Executing RAG query: {payload.query[:100]}... (session={session_id})")

    # Execute RAG query with retrieval + generation
    rag_response = rag_service.query(
        question=payload.query,
        top_k=payload.top_k,
        filters=payload.filters.attributes if payload.filters else None,
        include_sources=True,
    )

    logger.info(f"RAG query completed, retrieved {len(rag_response.get('sources', []))} sources")

    response = QueryResponse(
        answer=rag_response["answer"],
        sources=rag_response.get("sources", []),
        metadata={
            **rag_response.get("metadata", {}),
            "session_id": session_id,
        },
    )
    session_store.append_message(session_id, "assistant", response.answer)
    return jsonify(response.model_dump())


@api_bp.route("/sessions", methods=["POST"])
def create_session() -> Response:
    payload = _parse_payload(SessionCreateRequest)
    store = get_session_store()
    session_id = store.create_session(user_id=payload.user_id, metadata=payload.metadata)
    session = store.get_session(session_id)
    response = SessionResponse(
        session_id=session_id,
        metadata=session["metadata"],
        created_at=session["created_at"],
        user_id=session["user_id"],
        messages=session["messages"],
    )
    return jsonify(response.model_dump()), 201


@api_bp.route("/sessions/<session_id>", methods=["GET"])
def get_session(session_id: str) -> Response:
    store = get_session_store()
    session = store.get_session(session_id)
    if not session:
        error = ErrorResponse(code="not_found", message="Session not found", details=[{"session_id": session_id}])
        return jsonify({"error": error.dict()}), 404

    response = SessionResponse(
        session_id=session_id,
        metadata=session["metadata"],
        created_at=session["created_at"],
        user_id=session["user_id"],
        messages=session["messages"],
    )
    return jsonify(response.model_dump())


def _parse_payload(model):
    try:
        payload = request.get_json(force=True)
    except Exception as exc:  # pragma: no cover
        error = ErrorResponse(
            code="invalid_json",
            message="Unable to parse JSON payload",
            details=[{"error": str(exc)}],
        )
        raise _ValidationException(jsonify({"error": error.model_dump()}), 400)

    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        error = ErrorResponse(
            code="validation_error",
            message="Payload validation failed",
            details=exc.errors(),
        )
        raise _ValidationException(jsonify({"error": error.model_dump()}), 422)


def _resolve_document_bytes(document) -> bytes:
    if document.base64_encoded:
        return base64.b64decode(document.content or "")
    encoding = document.encoding or "utf-8"
    return (document.content or "").encode(encoding)


class _ValidationException(Exception):
    def __init__(self, response, status_code: int) -> None:
        self.response = response
        self.status_code = status_code


@api_bp.errorhandler(_ValidationException)
def handle_validation_exception(error: _ValidationException):  # pragma: no cover - blueprint scoped handler
    return error.response, error.status_code
