"""API endpoints for the RAG Automation Agent."""

from __future__ import annotations

import base64
import json
from typing import Any, Dict

from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context
from pydantic import ValidationError

from ..auth import current_client_id, rate_limit, require_api_key
from ..ingestion.config import ChunkingConfig
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
    try:
        es_health = get_vector_store().health_check()
    except Exception as exc:  # e.g. Elasticsearch down or index/model mismatch at startup
        logger.error(f"Health check failed: {exc}")
        es_health = {"status": "error"}
    healthy = es_health.get("status") in {"green", "yellow"}
    status = {
        "status": "ok" if healthy else "degraded",
        "version": current_app.config.get("APP_VERSION", "0.1.0"),
        "elasticsearch": es_health.get("status", "unknown"),
    }
    return jsonify(status), 200 if healthy else 503


@api_bp.route("/ingest", methods=["POST"])
@require_api_key
@rate_limit
@track_ingestion
def ingest_documents() -> Response:
    payload = _parse_payload(IngestRequest)
    pipeline = get_ingestion_pipeline()
    vector_store = get_vector_store()

    logger.info(f"Ingesting {len(payload.documents)} documents from source={payload.source}")

    chunking = None
    if payload.options:
        defaults = pipeline.chunking_service.config
        chunk_size = payload.options.chunk_size or defaults.chunk_size
        chunk_overlap = payload.options.chunk_overlap
        if chunk_overlap is None:
            chunk_overlap = min(defaults.chunk_overlap, chunk_size // 4)
        try:
            chunking = ChunkingConfig(chunk_size=chunk_size, chunk_overlap=chunk_overlap, language=payload.options.language)
        except ValueError as exc:
            error = ErrorResponse(code="validation_error", message=str(exc))
            return jsonify({"error": error.model_dump()}), 422

    created_documents = []
    for document in payload.documents:
        metadata = {**document.metadata}
        if document.mime_type:
            metadata.setdefault("mime_type", document.mime_type)

        if payload.source == "url" and document.url:
            result = pipeline.ingest_url(str(document.url), metadata=metadata, chunking=chunking)
        else:
            filename = document.filename or "document.txt"
            content_bytes = _resolve_document_bytes(document)
            result = pipeline.ingest_bytes(content=content_bytes, filename=filename, metadata=metadata, chunking=chunking)

        # Index chunks into Elasticsearch with embeddings
        doc_ids = vector_store.add_documents(result.chunks)
        logger.info(f"Indexed {len(doc_ids)} chunks for source={result.source}")

        created_documents.append(
            {
                "document_id": vector_store.document_id_for(result.source),
                "source": result.source,
                "chunks": len(result.chunks),
                "indexed_ids": doc_ids,
                "mime_type": result.mime_type,
            }
        )

    return jsonify({"status": "indexed", "documents": created_documents}), 202


@api_bp.route("/documents", methods=["GET"])
@require_api_key
@rate_limit
def list_documents() -> Response:
    try:
        size = min(max(int(request.args.get("size", 20)), 1), 100)
    except ValueError:
        size = 20
    page = get_vector_store().list_documents(size=size, after=request.args.get("after"))
    return jsonify(page)


@api_bp.route("/documents/<document_id>", methods=["GET"])
@require_api_key
@rate_limit
def get_document(document_id: str) -> Response:
    document = get_vector_store().get_document(document_id)
    if document is None:
        return _document_not_found(document_id)
    return jsonify(document)


@api_bp.route("/documents/<document_id>", methods=["DELETE"])
@require_api_key
@rate_limit
def delete_document(document_id: str) -> Response:
    deleted = get_vector_store().delete_document(document_id)
    if not deleted:
        return _document_not_found(document_id)
    logger.info(f"Deleted document {document_id} ({deleted} chunks)")
    return jsonify({"document_id": document_id, "deleted_chunks": deleted})


@api_bp.route("/query", methods=["POST"])
@require_api_key
@rate_limit
@track_query
def query_documents() -> Response:
    payload = _parse_payload(QueryRequest)
    session_store = get_session_store()
    rag_service = get_rag_service()
    owner = current_client_id()

    if payload.session_id:
        if not session_store.get_session(payload.session_id, owner=owner):
            return _session_not_found(payload.session_id)
        session_id = payload.session_id
    else:
        session_id = session_store.create_session(user_id=None, owner=owner)
    session_store.append_message(session_id, "user", payload.query)

    logger.info(f"Executing RAG query: {payload.query[:100]}... (session={session_id})")

    options = {
        "top_k": payload.top_k,
        "filters": payload.filters.attributes if payload.filters else None,
        "min_score": payload.min_score,
        "max_age_days": payload.max_age_days,
        "prompt_type": payload.prompt_type,
        "rerank": payload.rerank,
    }

    if payload.stream:
        return _stream_answer(rag_service, session_store, session_id, payload.query, options)

    # Execute RAG query with retrieval + generation
    rag_response = rag_service.query(question=payload.query, include_sources=True, **options)

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
@require_api_key
@rate_limit
def create_session() -> Response:
    payload = _parse_payload(SessionCreateRequest)
    store = get_session_store()
    owner = current_client_id()
    session_id = store.create_session(user_id=payload.user_id, metadata=payload.metadata, owner=owner)
    session = store.get_session(session_id, owner=owner)
    response = SessionResponse(
        session_id=session_id,
        metadata=session["metadata"],
        created_at=session["created_at"],
        user_id=session["user_id"],
        messages=session["messages"],
    )
    return jsonify(response.model_dump()), 201


@api_bp.route("/sessions/<session_id>", methods=["GET"])
@require_api_key
@rate_limit
def get_session(session_id: str) -> Response:
    store = get_session_store()
    session = store.get_session(session_id, owner=current_client_id())
    if not session:
        return _session_not_found(session_id)

    response = SessionResponse(
        session_id=session_id,
        metadata=session["metadata"],
        created_at=session["created_at"],
        user_id=session["user_id"],
        messages=session["messages"],
    )
    return jsonify(response.model_dump())


def _stream_answer(rag_service, session_store, session_id: str, question: str, options: Dict[str, Any]) -> Response:
    """Server-Sent Events: ``sources``, then ``token`` chunks, then ``done`` (or ``error``)."""

    def events():
        try:
            for event in rag_service.stream_query(question, **options):
                data = event["data"]
                if event["event"] == "done":
                    data = {**data, "metadata": {**data.get("metadata", {}), "session_id": session_id}}
                    session_store.append_message(session_id, "assistant", data["answer"])
                yield _sse(event["event"], data)
        except Exception as exc:  # headers are already sent: report the failure in-band
            logger.exception(f"Streaming query failed: {exc}")
            yield _sse("error", {"code": "internal_server_error", "message": "An internal error occurred"})

    response = Response(stream_with_context(events()), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _document_not_found(document_id: str):
    error = ErrorResponse(code="not_found", message="Document not found", details=[{"document_id": document_id}])
    return jsonify({"error": error.model_dump()}), 404


def _session_not_found(session_id: str):
    error = ErrorResponse(code="not_found", message="Session not found", details=[{"session_id": session_id}])
    return jsonify({"error": error.model_dump()}), 404


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
            # ctx can hold the raw exception object, which is not JSON serializable
            details=exc.errors(include_url=False, include_context=False),
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
