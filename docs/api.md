# API Reference

REST endpoints of the Flask interface. Every endpoint except `/health` and `/metrics` requires an API key in the `X-API-Key` header (when `AUTH_ENABLED=true`, the default).

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
    "stream": false
  }
  ```
  - `rerank`: force cross-encoder re-ranking on/off; omit for the server default.
  - `search_mode`: `dense` (kNN), `sparse` (BM25) or `hybrid` (both, fused with Reciprocal Rank Fusion); omit for `SEARCH_MODE` (default `hybrid`). `min_score` only applies to the dense part.
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

### `GET /documents?size=20&after=<document_id>`
- **Purpose**: List indexed source documents (one entry per document).
- **Response**: `{"documents": [{"document_id", "source", "mime_type", "chunks", "indexed_at"}], "next": "<cursor or null>"}`

### `GET /documents/{document_id}`
- **Response**: `{"document_id", "source", "chunks": [{"id", "content", "chunk_index"}]}`, or `404`.

### `DELETE /documents/{document_id}`
- **Response**: `{"document_id": "3f2a...", "deleted_chunks": 12}`, or `404`.

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
