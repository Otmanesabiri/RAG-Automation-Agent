# RAG Automation Agent

A Retrieval-Augmented Generation service: it ingests documents (PDF, DOCX, TXT, Markdown, HTML, web pages), indexes them in Elasticsearch, and answers questions with an LLM (Gemini, Claude or OpenAI) grounded in the retrieved passages, with sources.

## Capabilities

- **Ingestion**: file upload (raw or base64) or URL, with SSRF protection; automatic or per-request chunk sizing; re-ingesting a source replaces it instead of duplicating it.
- **Retrieval**: kNN vector search in Elasticsearch, metadata/age/permission filters, optional cross-encoder re-ranking.
- **Generation**: anti-hallucination prompt strategies (strict, citation, contradiction, confidence, structured), streamed answers (SSE), conversation memory with follow-up question rewriting.
- **API**: Flask REST API with API-key auth, per-client rate limiting and sessions (shared through Redis), document management endpoints.
- **Operations**: Prometheus metrics, Grafana dashboards, structured logging, Docker image, CI (tests, lint, security scans), Terraform for AWS staging.

## Quickstart

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env      # set LLM_PROVIDER + its API key, API_KEYS, ELASTICSEARCH_PASSWORD
docker compose up -d elasticsearch redis
python -m src.api.app     # http://localhost:8000
```

Full guide: [docs/QUICKSTART.md](docs/QUICKSTART.md) · API reference: [docs/api.md](docs/api.md)

## Repository structure

```
.
├── src/
│   ├── api/            # Flask app, routes, schemas, sessions
│   ├── auth/           # API-key auth, rate limiting
│   ├── ingestion/      # loaders, safe URL fetcher, chunking, pipeline
│   ├── embeddings/     # sentence-transformers / OpenAI embeddings
│   ├── vector_store/   # Elasticsearch store (bulk indexing, kNN, documents)
│   ├── retrieval/      # re-ranker, citation verifier
│   ├── llm/            # LLM providers, prompts, RAG orchestration
│   └── observability/  # Prometheus metrics, logging
├── tests/              # unit tests; tests/integration needs Elasticsearch
├── configs/            # Elasticsearch index template, Prometheus, Grafana, Alertmanager
├── scripts/            # index bootstrap; scripts/manual: checks needing real API keys
├── terraform/aws/      # staging infrastructure
├── docs/               # quickstart, API, architecture, observability, runbooks
├── requirements.in     # direct dependencies (requirements.txt is the generated lock)
└── docker-compose.yml  # Elasticsearch + Redis
```

## Roadmap

Work is tracked in [GitHub issues](https://github.com/Otmanesabiri/RAG-Automation-Agent/issues); the overview is in [issue #27](https://github.com/Otmanesabiri/RAG-Automation-Agent/issues/27).

| Priority | Theme | Status |
|----------|-------|--------|
| P0 | Security: secrets out of Git, auth/rate limit, SSRF, hardened config, current LLM models | Done (#1 still needs key rotation and history purge) |
| P1 | Reliability: Redis state, bulk indexing, index bootstrap, API params, conversation memory, metrics, tests, pinned dependencies, docs | Done |
| P2 | RAG quality: hybrid search, multi-query/HyDE, structural chunking, LLM citation check, automatic evaluation, business metrics | Planned (#15–#20) |
| P3 | Agent features: tool-calling agent, source connectors, multi-tenant RBAC, web UI, MCP server, automations | Planned (#21–#26) |

Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## Documentation

- [docs/QUICKSTART.md](docs/QUICKSTART.md): local setup, tests, troubleshooting
- [docs/api.md](docs/api.md): endpoints, streaming, errors
- [docs/architecture.md](docs/architecture.md) and [docs/architecture/](docs/architecture/): components and PlantUML diagrams
- [docs/data-flow.md](docs/data-flow.md): ingestion and query flows
- [docs/RAG_ENHANCEMENTS.md](docs/RAG_ENHANCEMENTS.md): re-ranking, prompts, filters, citation checks
- [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md), [docs/SLO_RUNBOOKS.md](docs/SLO_RUNBOOKS.md): monitoring and runbooks
- [docs/CICD.md](docs/CICD.md): pipelines
- [docs/SECURITY_CLEANUP.md](docs/SECURITY_CLEANUP.md): secret rotation and history cleanup
- [docs/history/](docs/history/): earlier phase reports

## Target scenarios

- **Enterprise knowledge bot**: « Quels sont les points clés du rapport financier Q2 2024 pour la région EMEA ? »
- **Compliance copilot**: « Quels documents mentionnent la politique de rétention des données et quelles exceptions existent ? »
- **Customer support agent**: « Réponds à la demande du client concernant la procédure de remboursement premium. »
- **Operations analyst**: « Résume les incidents critiques de la semaine dernière et propose des mesures d'atténuation. »

## Contributing

1. Create a branch from `main` per issue, open a pull request referencing it (`Closes #N`).
2. Run `pytest` and `pre-commit install` (gitleaks blocks committed secrets).
3. CI must pass: tests (coverage ≥ 75%), lint, security scans, Docker build.

## Contact

Open an issue on the repository.
