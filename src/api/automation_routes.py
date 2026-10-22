"""Automation endpoints: digests, watches, structured extraction and signed webhooks."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flask import Blueprint, Response, jsonify, request

from ..auth import current_client_id, current_principal, rate_limit, require_api_key, require_role, search_scope
from ..auth.identity import DEFAULT_TENANT, multi_tenant_enabled
from ..automations import build_digest, check_schema, extract, run_watches, verify_webhook_signature
from ..observability import get_logger
from ..observability.audit import audit
from .dependencies import (
    get_connectors,
    get_ingestion_pipeline,
    get_job_manager,
    get_llm_service_cached,
    get_notifier,
    get_rag_service,
    get_sync_service,
    get_vector_store,
    get_watch_store,
)
from .routes import _document_not_found, _parse_payload
from .schemas import DigestRequest, ErrorResponse, ExtractRequest, WatchRequest

automation_bp = Blueprint("automations", __name__)
logger = get_logger(__name__)


def _tenant() -> str:
    principal = current_principal()
    return principal.tenant if principal and multi_tenant_enabled() else DEFAULT_TENANT


def _scope_kwargs() -> dict:
    filters, levels = search_scope(current_principal())
    return {"filters": filters or None, "user_permissions": levels}


def _error(code: str, message: str, status: int):
    return jsonify({"error": ErrorResponse(code=code, message=message).model_dump()}), status


@automation_bp.route("/automations/digest", methods=["POST"])
@require_api_key
@require_role("admin")
@rate_limit
def run_digest() -> Response:
    """Summarise documents indexed in the last ``since_hours`` and send them to the notification channels."""
    payload = _parse_payload(DigestRequest)
    since = datetime.now(timezone.utc) - timedelta(hours=payload.since_hours)
    store, llm, notifier, scope = get_vector_store(), get_llm_service_cached(), get_notifier(), _scope_kwargs()
    audit("digest", since_hours=payload.since_hours)

    def job(progress):
        digest = build_digest(store, llm, since=since, scope=scope)
        delivered = notifier.send(digest["subject"], digest["body"]) if payload.notify and digest["documents"] else []
        return {**digest, "delivered": delivered}

    return jsonify({"status": "queued", "job_id": get_job_manager().submit("digest", job, owner=current_client_id())}), 202


@automation_bp.route("/watches", methods=["GET"])
@require_api_key
@rate_limit
def list_watches() -> Response:
    return jsonify({"watches": get_watch_store().list(_tenant())})


@automation_bp.route("/watches", methods=["POST"])
@require_api_key
@require_role("editor")
@rate_limit
def create_watch() -> Response:
    payload = _parse_payload(WatchRequest)
    watch = get_watch_store().create(_tenant(), payload.name, payload.query, payload.filters, payload.min_score)
    audit("create_watch", watch_id=watch["id"], name=payload.name)
    return jsonify(watch), 201


@automation_bp.route("/watches/<watch_id>", methods=["DELETE"])
@require_api_key
@require_role("editor")
@rate_limit
def delete_watch(watch_id: str) -> Response:
    if not get_watch_store().delete(_tenant(), watch_id):
        return _error("not_found", "Watch not found", 404)
    audit("delete_watch", watch_id=watch_id)
    return jsonify({"deleted": watch_id})


@automation_bp.route("/automations/watches/run", methods=["POST"])
@require_api_key
@require_role("admin")
@rate_limit
def trigger_watches() -> Response:
    """Check all watches now (normally run on a schedule by scripts/run_automations.py)."""
    store, rag, notifier = get_watch_store(), get_rag_service(), get_notifier()
    scope = (lambda tenant: {"tenant": tenant}) if multi_tenant_enabled() else None
    job_id = get_job_manager().submit(
        "watches",
        lambda progress: {"alerts": run_watches(store, rag, notifier, scope_for_tenant=scope)},
        owner=current_client_id(),
    )
    return jsonify({"status": "queued", "job_id": job_id}), 202


@automation_bp.route("/extract", methods=["POST"])
@require_api_key
@rate_limit
def extract_structured() -> Response:
    """Extract JSON matching ``schema`` from a document (or raw text)."""
    payload = _parse_payload(ExtractRequest)
    ok, message = check_schema(payload.json_schema)
    if not ok:
        return _error("validation_error", message, 422)
    text = payload.text
    if payload.document_id:
        document = get_vector_store().get_document(payload.document_id, **_scope_kwargs())
        if document is None:
            return _document_not_found(payload.document_id)
        text = "\n".join(chunk["content"] for chunk in document["chunks"])
    audit("extract", document_id=payload.document_id)
    return jsonify(extract(get_llm_service_cached(), text, payload.json_schema, payload.instructions))


@automation_bp.route("/webhooks/ingest", methods=["POST"])
@rate_limit
def webhook_ingest() -> Response:
    """Incoming webhook (e.g. from a CMS or CI): ``{"url": ...}`` or ``{"connector": ...}``.

    Authenticated by an HMAC signature of the raw body (X-Signature: sha256=...)
    with WEBHOOK_SECRET instead of an API key.
    """
    if not verify_webhook_signature(request.get_data(), request.headers.get("X-Signature")):
        return _error("invalid_signature", "Missing or invalid webhook signature", 401)
    body = request.get_json(silent=True) or {}
    jobs = get_job_manager()
    if body.get("connector"):
        connector = get_connectors().get(body["connector"])
        if connector is None:
            return _error("not_found", "Connector not found", 404)
        sync_service = get_sync_service()
        job_id = jobs.submit("sync", lambda p: sync_service.sync(connector, progress=p).as_dict(), owner="webhook")
    elif body.get("url"):
        pipeline, store, url = get_ingestion_pipeline(), get_vector_store(), str(body["url"])

        def ingest(progress):
            result = pipeline.ingest_url(url, metadata={"ingested_by": "webhook"})
            store.add_documents(result.chunks)
            return {"source": result.source, "chunks": len(result.chunks)}

        job_id = jobs.submit("ingest", ingest, owner="webhook")
    else:
        return _error("validation_error", "Body must contain 'url' or 'connector'", 422)
    audit("webhook", target=body.get("connector") or body.get("url"))
    return jsonify({"status": "queued", "job_id": job_id}), 202
