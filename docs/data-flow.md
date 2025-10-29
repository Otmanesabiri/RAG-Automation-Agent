# Data Flow Specification

This document complements `architecture.md` by detailing the operational steps and configurations for the ingestion, indexing, retrieval, and generation pipeline.

## 1. Ingestion
1. Client uploads file or provides URL via `/ingest`.
2. Loader selection matrix resolves to specific LangChain document loader.
3. Content normalized to UTF-8 text; non-text assets flagged for future OCR pipeline.
4. Metadata builder attaches:
   - `source` (upload, s3, sharepoint, etc.)
   - `owner` and `department`
   - `timestamp_ingested`
   - `retention_policy`

## 2. Chunking Strategy
- Default chunk size: **800 tokens** with **120 token overlap**.
- Dynamic adjustment:
  - Long-form PDFs (>50 pages) → chunk size 1,000.
  - Short memos (<5 pages) → chunk size 500.
- Language-aware splitters for French, English, Arabic via spaCy optional models.

## 3. Embedding Pipeline
- Batching: 32 chunks/batch (configurable) with exponential backoff retry (max 3 attempts).
- GPU acceleration automatically used when CUDA device detected.
- Embedding records include hash fingerprint to deduplicate identical content.

## 4. Elasticsearch Index Configuration
- Index name: `rag_documents` (configurable via env var).
- Mapping (simplified):
  ```json
  {
    "mappings": {
      "properties": {
        "id": {"type": "keyword"},
        "content": {"type": "text", "analyzer": "standard"},
        "metadata": {"type": "object", "enabled": true},
        "embedding": {"type": "dense_vector", "dims": 1536, "index": true, "similarity": "cosine"},
        "created_at": {"type": "date"}
      }
    }
  }
  ```
- Shards: `1` (dev) → `3` (staging) with `1` replica for HA.
- Refresh interval tuned to `5s` during bulk ingest, `1s` otherwise.

## 5. Retrieval Workflow
1. Query normalized (lowercase, trimmed) with optional synonyms expansion.
2. Retriever executes kNN search (`k=5`) using cosine similarity.
3. Optional re-ranker (CrossEncoder) reranks top 20 hits down to top 3.
4. Metadata filters applied: department, document_type, classification.
5. Results packaged with snippet generation (highlight window 40 tokens).

## 6. Generation Workflow
1. Prompt template:
   ```
   You are an enterprise assistant...
   Context:
   {context}
   Question:
   {question}
   ```
2. LLM selection rules:
   - High sensitivity topics → on-prem model.
   - General queries → OpenAI `gpt-4o`.
3. Memory storage for session if `session_id` provided.
4. Response post-processing:
   - Append citations `[source: doc_id]`.
   - Produce structured JSON when client requests `response_format=json`.

## 7. Monitoring Hooks
- Metrics recorded:
  - `ingest_duration_seconds`
  - `embedding_tokens_total`
  - `retrieval_hits_total`
  - `generation_latency_seconds`
- Logs enriched with request ID, user ID, document IDs, and latency breakdown.

## 8. Failure Handling
- Dead-letter queue (future) for documents failing ingestion after 3 retries.
- Alerting when kNN search returns <2 results for high-priority departments.
- Token usage budgets enforced per session to control costs.
