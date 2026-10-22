# Changelog

## Unreleased

### Security
- `.env` and logs are no longer tracked; gitleaks pre-commit hook (#1).
- Authentication runs before rate limiting; `/sessions` requires auth and sessions are private to their API key (#2).
- URL ingestion blocks private/internal targets, re-validates redirects and bounds response size (#3).
- Production refuses default secrets; CORS off unless configured; Elasticsearch TLS verified; 500 responses hide internals (#4).

### Added
- Redis-backed sessions and rate limiting for multi-worker deployments (#6).
- `GET/DELETE /documents` endpoints; idempotent re-ingestion (#7).
- Index created on startup with the embedding model's dimension (#8).
- `/query`: `rerank`, `prompt_type`, `min_score`, `max_age_days`, SSE streaming; `/ingest` chunking options (#9).
- Conversation memory: follow-up questions rewritten from session history (#10).
- Unit and Elasticsearch integration test suites, coverage gate in CI (#12).

### Changed
- Default models: `claude-opus-5-5`, `gemini-2.5-flash`, `gpt-4.1`, configurable per provider; timeouts and retries (#5).
- Bulk indexing instead of one request per chunk (#7).
- Pinned dependency lock with CPU-only PyTorch, multi-stage Docker image, `unstructured` optional (#13).
- Documentation consolidated: single quickstart, updated README and API reference, phase reports moved to `docs/history/` (#14).

### Fixed
- Ingestion and embedding-token Prometheus metrics were never incremented (#11).
- Uploaded documents were cited under a random temp path; temp file descriptors leaked.
- Validation errors from custom validators returned 500.
- Re-ranked sources lost their chunk ids.
