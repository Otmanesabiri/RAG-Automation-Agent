"""API endpoints for the RAG Automation Agent."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Dict

from flask import Blueprint, Response, current_app, jsonify, request, send_from_directory, stream_with_context
from pydantic import ValidationError

from ..auth import current_client_id, current_principal, rate_limit, require_api_key, require_role, search_scope
from ..auth.identity import multi_tenant_enabled
from ..ingestion.config import ChunkingConfig
from ..observability import get_logger, track_ingestion, track_query
from ..observability.audit import audit
from .dependencies import (
    get_agent_runner,
    get_connectors,
    get_feedback_store,
    get_ingestion_pipeline,
    get_job_manager,
    get_rag_service,
    get_session_store,
    get_sync_service,
    get_vector_store,
)
from .schemas import (
    AgentRequest,
    ErrorResponse,
    FeedbackRequest,
    IngestRequest,
    QueryRequest,
    QueryResponse,
    SearchRequest,
    SessionCreateRequest,
    SessionResponse,
)

api_bp = Blueprint("api", __name__)
STATIC_DIR = Path(__file__).resolve().parent / "static"
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
@require_role("editor")
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

    tenant = current_principal().tenant if multi_tenant_enabled() else None
    audit("ingest", documents=len(payload.documents), source_type=payload.source)
    if request.args.get("async", "").lower() == "true":
        job_id = get_job_manager().submit(
            "ingest",
            lambda progress: {"documents": _ingest_documents(payload, pipeline, vector_store, chunking, tenant)},
            owner=current_client_id(),
        )
        return jsonify({"status": "queued", "job_id": job_id}), 202

    created_documents = _ingest_documents(payload, pipeline, vector_store, chunking, tenant)
    return jsonify({"status": "indexed", "documents": created_documents}), 202


def _ingest_documents(payload, pipeline, vector_store, chunking, tenant=None) -> list:
    created_documents = []
    for document in payload.documents:
        metadata = {**document.metadata}
        if tenant:  # set by the server from the caller's identity, never by the client
            metadata["tenant"] = tenant
        else:
            metadata.pop("tenant", None)
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
                "document_id": vector_store.document_id_for(result.source, tenant),
                "source": result.source,
                "chunks": len(result.chunks),
                "indexed_ids": doc_ids,
                "mime_type": result.mime_type,
            }
        )
    return created_documents


@api_bp.route("/connectors", methods=["GET"])
@require_api_key
@rate_limit
def list_connectors() -> Response:
    connectors = get_connectors()
    return jsonify({"connectors": [{"name": name, "type": type(c).__name__} for name, c in connectors.items()]})


@api_bp.route("/connectors/<name>/sync", methods=["POST"])
@require_api_key
@require_role("admin")
@rate_limit
def sync_connector(name: str) -> Response:
    """Start an incremental sync of a configured connector; returns a job id."""
    connector = get_connectors().get(name)
    if connector is None:
        error = ErrorResponse(code="not_found", message="Connector not found", details=[{"connector": name}])
        return jsonify({"error": error.model_dump()}), 404
    full = bool((request.get_json(silent=True) or {}).get("full", False))
    audit("connector_sync", connector=name, full=full)
    sync_service = get_sync_service()
    job_id = get_job_manager().submit(
        "sync",
        lambda progress: sync_service.sync(connector, full=full, progress=progress).as_dict(),
        owner=current_client_id(),
    )
    return jsonify({"status": "queued", "job_id": job_id, "connector": name}), 202


@api_bp.route("/jobs/<job_id>", methods=["GET"])
@require_api_key
@rate_limit
def get_job(job_id: str) -> Response:
    job = get_job_manager().get(job_id, owner=current_client_id())
    if job is None:
        error = ErrorResponse(code="not_found", message="Job not found", details=[{"job_id": job_id}])
        return jsonify({"error": error.model_dump()}), 404
    return jsonify(job)


@api_bp.route("/documents", methods=["GET"])
@require_api_key
@rate_limit
def list_documents() -> Response:
    try:
        size = min(max(int(request.args.get("size", 20)), 1), 100)
    except ValueError:
        size = 20
    filters, levels = search_scope(current_principal())
    page = get_vector_store().list_documents(
        size=size, after=request.args.get("after"), filters=filters, user_permissions=levels
    )
    return jsonify(page)


@api_bp.route("/documents/<document_id>", methods=["GET"])
@require_api_key
@rate_limit
def get_document(document_id: str) -> Response:
    filters, levels = search_scope(current_principal())
    document = get_vector_store().get_document(document_id, filters=filters, user_permissions=levels)
    if document is None:
        return _document_not_found(document_id)
    return jsonify(document)


@api_bp.route("/documents/<document_id>", methods=["DELETE"])
@require_api_key
@require_role("admin")
@rate_limit
def delete_document(document_id: str) -> Response:
    filters, levels = search_scope(current_principal())
    deleted = get_vector_store().delete_document(document_id, filters=filters, user_permissions=levels)
    audit("delete_document", document_id=document_id, deleted_chunks=deleted)
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

    history = []
    if payload.session_id:
        session = session_store.get_session(payload.session_id, owner=owner)
        if not session:
            return _session_not_found(payload.session_id)
        session_id = payload.session_id
        history = list(session["messages"])
    else:
        session_id = session_store.create_session(user_id=None, owner=owner)
    session_store.append_message(session_id, "user", payload.query)

    logger.info(f"Executing RAG query: {payload.query[:100]}... (session={session_id})")

    # The caller's tenant and access levels are enforced on top of its own filters.
    scope_filters, levels = search_scope(current_principal())
    user_filters = (payload.filters.attributes if payload.filters else None) or {}
    options = {
        "top_k": payload.top_k,
        "filters": {**user_filters, **scope_filters} or None,
        "user_permissions": levels,
        "min_score": payload.min_score,
        "max_age_days": payload.max_age_days,
        "prompt_type": payload.prompt_type,
        "rerank": payload.rerank,
        "search_mode": payload.search_mode,
        "query_expansion": payload.query_expansion,
        "verify_citations": payload.verify_citations,
        "history": history,
    }

    if payload.stream:
        audit("query", session_id=session_id, stream=True)
        return _stream_answer(rag_service, session_store, session_id, payload.query, options)

    audit("query", session_id=session_id, stream=False)
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
        citation_check=rag_response.get("citation_check"),
    )
    session_store.append_message(session_id, "assistant", response.answer)
    return jsonify(response.model_dump())


@api_bp.route("/search", methods=["POST"])
@require_api_key
@rate_limit
def search_documents() -> Response:
    """Retrieval only (no LLM): the passages /query would give to the model."""
    payload = _parse_payload(SearchRequest)
    scope_filters, levels = search_scope(current_principal())
    filters = {**(payload.filters.attributes or {}), **scope_filters} or None
    results, reranked = get_rag_service().retrieve(
        payload.query,
        top_k=payload.top_k,
        filters=filters,
        user_permissions=levels,
        rerank=payload.rerank,
        search_mode=payload.search_mode,
    )
    return jsonify(
        {
            "results": [
                {
                    "chunk_id": r["id"],
                    "document_id": r.get("document_id"),
                    "source": r.get("source"),
                    "section": (r.get("metadata") or {}).get("section"),
                    "score": r.get("score"),
                    "content": r.get("content"),
                }
                for r in results
            ],
            "reranked": reranked,
        }
    )


@api_bp.route("/agent", methods=["POST"])
@require_api_key
@rate_limit
@track_query
def run_agent() -> Response:
    """Multi-step research: the LLM searches and reads documents with tools before answering."""
    payload = _parse_payload(AgentRequest)
    session_store = get_session_store()
    owner = current_client_id()

    history = []
    if payload.session_id:
        session = session_store.get_session(payload.session_id, owner=owner)
        if not session:
            return _session_not_found(payload.session_id)
        session_id = payload.session_id
        history = list(session["messages"])
    else:
        session_id = session_store.create_session(user_id=None, owner=owner)
    session_store.append_message(session_id, "user", payload.question)

    audit("agent", session_id=session_id)
    result = get_agent_runner().run(payload.question, max_iterations=payload.max_iterations, history=history)
    session_store.append_message(session_id, "assistant", result.answer)
    logger.info(f"Agent finished in {result.iterations} iteration(s), {len(result.steps)} tool call(s)")
    return jsonify({**result.as_dict(), "session_id": session_id})


@api_bp.route("/ui", methods=["GET"])
def chat_ui() -> Response:
    """Single-page chat interface (the API key is entered in the page)."""
    response = send_from_directory(STATIC_DIR, "index.html")
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@api_bp.route("/feedback", methods=["POST"])
@require_api_key
@rate_limit
def add_feedback() -> Response:
    payload = _parse_payload(FeedbackRequest)
    principal = current_principal()
    entry = get_feedback_store().add(
        {
            **payload.model_dump(),
            "client_id": current_client_id(),
            "tenant": principal.tenant if principal else None,
        }
    )
    return jsonify({"status": "recorded", "created_at": entry["created_at"]}), 201


@api_bp.route("/feedback", methods=["GET"])
@require_api_key
@require_role("admin")
@rate_limit
def list_feedback() -> Response:
    try:
        limit = min(max(int(request.args.get("limit", 50)), 1), 500)
    except ValueError:
        limit = 50
    principal = current_principal()
    tenant = principal.tenant if principal and multi_tenant_enabled() else None
    return jsonify({"feedback": get_feedback_store().recent(limit, tenant=tenant)})


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
