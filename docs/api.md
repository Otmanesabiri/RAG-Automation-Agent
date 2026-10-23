# API Reference

REST endpoints of the Flask interface. Every endpoint except `/health` and `/metrics` requires authentication (when `AUTH_ENABLED=true`, the default):

- **API key** in `X-API-Key`: a service identity, `admin` role, default tenant, all documents (restrict individual keys with `API_KEY_SCOPES`).
- **JWT** in `Authorization: Bearer <token>` when `JWT_JWKS_URL` (OIDC) or `JWT_SECRET` is set: tenant, roles and document access levels come from the token claims, never from the payload.

Roles: `reader` (query, agent, read documents), `editor` (+ ingest), `admin` (+ delete documents, sync connectors). With `MULTI_TENANT=true`, ingested documents are stamped with the caller's tenant and every search, listing and deletion is limited to it; documents with a `metadata.access_level` are only visible to callers holding that level. Actions are written to the audit log (`AUDIT_LOG_FILE`).

## Base URL
- Local development: `http://localhost:8000`
- Staging: `https://staging.rag-agent.example.com`

## Endpoints

### `GET /health`
- **Purpose**: Liveness and readiness probe for orchestrators and load balancers.
- **Response**: `200 { "status": "ok", "version": "0.1.0", "elasticsearch": "green" }`, or `503` with `"status": "degraded"` when Elasticsearch is unreachable.
- **Auth**: Not required.

### `POST /ingest`
- **Purpose**: Ingest documents (raw text, base64 file content, or a URL).
- **Payload**:
  ```json
  {
    "source": "upload",
    "documents": [
      {
        "content": "<base64 | raw text>",
        "base64_encoded": true,
        "filename": "manual.pdf",
        "mime_type": "application/pdf",
        "metadata": {"department": "finance", "access_level": "internal"}
      }
    ],
    "options": {"chunk_size": 800, "chunk_overlap": 100, "language": "fr"}
  }
  ```
  With `"source": "url"`, each document carries `"url"` instead of `content`. URLs must resolve to public addresses (see `INGEST_URL_ALLOWLIST`).
- **Options**: `chunk_size` (100-2000) and `chunk_overlap` (< `chunk_size`) override the automatic chunk sizing for this request.
- **Response** (`202`):
  ```json
  {"status": "indexed", "documents": [{"document_id": "3f2a...", "source": "manual.pdf", "chunks": 12, "indexed_ids": ["..."], "mime_type": "application/pdf"}]}
  ```
  Re-ingesting the same source (filename or URL) replaces its previous chunks.

### `POST /query`
- **Purpose**: Retrieval-augmented question answering.
- **Payload**:
  ```json
  {
    "query": "Résumé du rapport de conformité 2024",
    "session_id": "optional-session-id",
    "top_k": 4,
    "filters": {"attributes": {"department": "compliance"}},
    "rerank": true,
    "prompt_type": "strict",
    "min_score": 0.5,
    "max_age_days": 365,
    "search_mode": "hybrid",
    "query_expansion": "multi_query",
    "verify_citations": "llm",
    "stream": false
  }
  ```
  - `rerank`: force cross-encoder re-ranking on/off; omit for the server default.
  - `search_mode`: `dense` (kNN), `sparse` (BM25) or `hybrid` (both, fused with Reciprocal Rank Fusion); omit for `SEARCH_MODE` (default `hybrid`). `min_score` only applies to the dense part.
  - `query_expansion`: `none`, `multi_query` (the LLM writes reformulations, all searched and fused) or `hyde` (the LLM writes a hypothetical answer passage that is searched too). Each adds one LLM call; `metadata.expanded_queries` lists the extra queries.
  - `verify_citations`: `off`, `lexical` (fuzzy text matching) or `llm` (an LLM judge checks each claim against the sources). When enabled, the response has a `citation_check` object: `mode`, `is_grounded`, `confidence` (share of supported claims), `ungrounded_claims`, `warnings`. Omit for `CITATION_CHECK`.
  - `prompt_type`: `default`, `strict`, `citation`, `contradiction`, `confidence`, `structured`.
  - `session_id`: must belong to the caller; omit to start a new session.
- **Response**:
  ```json
  {
    "answer": "Le rapport souligne...",
    "sources": [{"document_id": "3f2a...", "chunk_id": "9c1e...", "snippet": "...", "score": 0.82, "source": "rapport.pdf"}],
    "metadata": {"top_k": 4, "retrieved_docs": 4, "reranking_enabled": true, "prompt_type": "strict", "session_id": "..."}
  }
  ```
- **Streaming** (`"stream": true`): the response is `text/event-stream` with events
  `sources` (list of sources), `token` (text chunk, repeated), then `done` (`{"answer", "metadata"}`), or `error` if generation fails mid-stream.

### `POST /search`
- **Purpose**: retrieval only (no LLM call): the passages `/query` would give to the model.
- **Payload**: `{"query": "...", "top_k": 5, "search_mode": "hybrid", "rerank": null, "filters": {"attributes": {}}}`
- **Response**: `{"results": [{"chunk_id", "document_id", "source", "section", "score", "content"}], "reranked": false}`. Tenant and access-level restrictions apply.

### `POST /agent`
- **Purpose**: multi-step research. The LLM chooses tools (`search_documents`, `get_document`, `list_sources`, `summarize_document`) until it can answer; useful for comparisons and multi-part questions. Uses Claude's native tool use with `LLM_PROVIDER=anthropic`, a JSON action loop with other providers.
- **Payload**: `{"question": "Compare the refund and shipping policies", "session_id": "optional", "max_iterations": 6}` (1-15, default `AGENT_MAX_ITERATIONS`).
- **Response**:
  ```json
  {"answer": "...", "steps": [{"tool": "search_documents", "input": {"query": "refund policy"}, "output_preview": "...", "is_error": false}], "iterations": 3, "stopped_reason": "answered", "session_id": "..."}
  ```
  `stopped_reason` is `answered`, `max_iterations` or `max_tokens`.

### Connectors and background jobs
Sources are declared server-side in the JSON file at `CONNECTORS_CONFIG` (types `local`, `s3`, `confluence`; see `configs/connectors.example.json`). The API never accepts paths or URLs for them.
- `GET /connectors`: configured connectors.
- `POST /connectors/{name}/sync` (body `{"full": false}` optional): starts an incremental sync (new and changed documents indexed, removed ones deleted) and returns `202 {"job_id"}`.
- `POST /ingest?async=true`: same payload as `/ingest`, processed in the background; returns `202 {"job_id"}`.
- `GET /jobs/{job_id}`: `status` (`queued`, `running`, `succeeded`, `failed`), `progress`, `result` or `error`. Jobs are visible only to the API key that started them.

For scheduled syncs, run `python scripts/sync_sources.py [name...]` from cron or a Kubernetes CronJob.

### Automations
- `POST /automations/digest` (admin) `{"since_hours": 24, "notify": true}`: job that summarises the documents indexed in that window and sends the digest to `NOTIFY_WEBHOOK_URL` / `NOTIFY_EMAIL_TO`.
- `GET /watches`, `POST /watches` (editor) `{"name", "query", "filters", "min_score"}`, `DELETE /watches/{id}`: saved queries per tenant. `POST /automations/watches/run` (admin) checks them now; each watch alerts on passages indexed since its previous check.
- `POST /extract` `{"document_id" | "text", "schema": <JSON Schema object>, "instructions"}`: returns `{"data", "valid", "errors", "attempts"}`; the LLM gets one retry with the validation errors.
- `POST /webhooks/ingest` `{"url"}` or `{"connector"}`: for external systems (CMS, CI). Authenticated with `X-Signature: sha256=<hex HMAC-SHA256 of the raw body with WEBHOOK_SECRET>` instead of an API key; returns a job id.

Schedules: `python scripts/run_automations.py digest --since-hours 24` and `python scripts/run_automations.py watches` from cron.

### `GET /documents?size=20&after=<document_id>`
- **Purpose**: List indexed source documents (one entry per document).
- **Response**: `{"documents": [{"document_id", "source", "mime_type", "chunks", "indexed_at"}], "next": "<cursor or null>"}`

### `GET /documents/{document_id}`
- **Response**: `{"document_id", "source", "chunks": [{"id", "content", "chunk_index"}]}`, or `404`.

### `DELETE /documents/{document_id}`
- **Response**: `{"document_id": "3f2a...", "deleted_chunks": 12}`, or `404`.

### `GET /ui`
Chat page (no build step): enter an API key or a JWT, ask questions (streamed answers with sources) or switch to agent mode, upload a document, rate answers 👍/👎.

### `POST /feedback`
- **Payload**: `{"rating": "up" | "down", "session_id", "question", "answer", "comment"}` (`201`). Counted in `rag_agent_feedback_total{rating}`.
- `GET /feedback?limit=50` (admin): most recent entries, limited to the caller's tenant in multi-tenant mode.

### `POST /sessions`
- **Payload**: `{ "user_id": "u-42", "metadata": {"locale": "fr-FR"} }`
- **Response** (`201`): the session, including `session_id`.

### `GET /sessions/{session_id}`
- **Response**: the session with its messages; `404` if it does not exist or belongs to another API key.

## Error Handling
- Structured error body:
  ```json
  {"error": {"code": "validation_error", "message": "Payload validation failed", "details": [{"loc": ["options", "chunk_size"], "msg": "..."}]}}
  ```
- Status codes: 400 (`unsafe_url`), 401/403 (auth), 404 (not found), 415 (unsupported document type), 422 (validation, `llm_refusal`), 429 (rate limit), 500 (unexpected, no internal details), 503 (dependency down).

## Rate Limiting
- `RATE_LIMIT_RPM` requests/minute per API key (default 60), per IP when auth is disabled.
- Shared across workers through Redis when `REDIS_URL` is set; in-memory otherwise.

## Versioning Strategy
- URL versioning when breaking changes occur (`/v1/query`).
