# API Design (Draft)

This document outlines the planned REST endpoints for the Flask interface. All endpoints require HTTPS and authentication via API key (header `X-API-Key`) or JWT (header `Authorization: Bearer <token>`).

## Base URL
- Local development: `http://localhost:8000`
- Staging: `https://staging.rag-agent.example.com`

## Endpoints

### `GET /health`
- **Purpose**: Liveness and readiness probe for orchestrators and load balancers.
- **Response**: `{ "status": "ok", "version": "v0.1.0", "elasticsearch": "green" }`
- **Auth**: Not required (read-only health info).

### `POST /ingest`
- **Purpose**: Trigger ingestion of new documents.
- **Payload**:
  ```json
  {
    "source": "upload" | "url",
    "documents": [
      {
        "content": "<base64 | raw text>",
        "filename": "manual.pdf",
        "mime_type": "application/pdf",
        "metadata": {
          "department": "finance",
          "tags": ["q2", "budget"],
          "visibility": "internal"
        }
      }
    ],
    "options": {
      "chunk_size": 800,
      "chunk_overlap": 100,
      "language": "fr"
    }
  }
  ```
- **Response**: Batch ingestion receipt with document IDs and status.
- **Auth**: Required.

### `POST /query`
- **Purpose**: Execute a retrieval-augmented question answering request.
- **Payload**:
  ```json
  {
    "query": "Résumé du rapport de conformité 2024",
    "session_id": "optional-session-id",
    "top_k": 4,
    "filters": {
      "department": "compliance"
    },
    "rerank": true,
    "stream": false
  }
  ```
- **Response**:
  ```json
  {
    "answer": "Le rapport souligne...",
    "sources": [
      {"document_id": "doc_123", "snippet": "...", "score": 0.82}
    ],
    "metadata": {
      "tokens_prompt": 1580,
      "tokens_completion": 320,
      "latency_ms": 2450
    }
  }
  ```
- **Auth**: Required.

### `POST /sessions`
- **Purpose**: Start or update a conversational session state.
- **Payload**: `{ "user_id": "u-42", "metadata": {"locale": "fr-FR"} }`
- **Response**: `{ "session_id": "sess_a1b2" }`
- **Auth**: Required.

### `GET /sessions/{session_id}`
- **Purpose**: Retrieve conversation history snapshot.
- **Response**: Array of messages with speaker roles and timestamps.
- **Auth**: Required.

### `DELETE /documents/{document_id}`
- **Purpose**: Soft-delete or hard-delete a document from the index.
- **Response**: `{ "document_id": "doc_123", "status": "deleted" }`
- **Auth**: Required; admin role only.

## Error Handling
- Structured error body:
  ```json
  {
    "error": {
      "code": "validation_error",
      "message": "chunk_size must be <= 1200",
      "details": [
        {"field": "options.chunk_size", "issue": "value_too_large"}
      ]
    }
  }
  ```
- Standard HTTP status codes: 400 (validation), 401/403 (auth), 404 (not found), 429 (rate limit), 500 (unexpected).

## Rate Limiting
- Default: 60 requests/minute per API key.
- Burst protection via Redis or in-memory fallback.

## Versioning Strategy
- URL versioning when breaking changes occur (`/v1/query`).
- Semantic version tags on responses for client awareness.

## Future Enhancements
- WebSocket streaming for low-latency token push.
- Async ingest pipeline with background jobs (Celery + Redis).
- Service-to-service authentication via mTLS.
