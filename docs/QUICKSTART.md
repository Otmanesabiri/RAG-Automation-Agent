# Quickstart

Run the RAG Automation Agent locally: Elasticsearch + Redis in Docker, the API in a Python virtualenv.

## Prerequisites

- Python 3.12
- Docker with Docker Compose
- An API key for one LLM provider (Gemini, Anthropic or OpenAI). Embeddings run locally with sentence-transformers by default.

## 1. Install

```bash
git clone https://github.com/Otmanesabiri/RAG-Automation-Agent.git
cd RAG-Automation-Agent
python3.12 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt   # runtime lock + test tools
```

`requirements.txt` is a lock file generated from `requirements.in` (CPU-only PyTorch). Formats beyond PDF/DOCX/TXT/Markdown/HTML need `pip install -r requirements-extras.txt`.

## 2. Configure

```bash
cp .env.example .env
```

Edit `.env` and set at least:

| Variable | Purpose |
|----------|---------|
| `LLM_PROVIDER` + matching `*_API_KEY` | `gemini` (default), `anthropic` or `openai` |
| `API_KEYS` | Comma-separated keys accepted in the `X-API-Key` header |
| `ELASTICSEARCH_PASSWORD` | Password of the local Elasticsearch (used by docker-compose too) |
| `SECRET_KEY` | Random value (`python -c "import secrets; print(secrets.token_hex(32))"`) |

Keep `FLASK_ENV=development` locally. Any other value is treated as production: the app refuses to start with default secrets, without `API_KEYS`, or with `AUTH_ENABLED=false`.

All settings are documented in [.env.example](../.env.example).

## 3. Start the services

```bash
docker compose up -d elasticsearch redis
```

The index is created automatically on first use, sized for the embedding model. To create it ahead of time: `python scripts/init_elasticsearch.py`.

## 4. Run the API

```bash
python -m src.api.app        # development server on http://localhost:8000
# or, as in the Docker image:
gunicorn --bind 0.0.0.0:8000 --workers 4 "src.api.app:create_app()"
```

The first request loads the embedding and re-ranking models (downloaded once into the Hugging Face cache).

## 5. Try it

```bash
KEY=<one of API_KEYS>

curl -s http://localhost:8000/health

curl -s -X POST http://localhost:8000/ingest -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"source": "upload", "documents": [{"content": "Paris is the capital of France.", "filename": "geo.txt"}]}'

curl -s -X POST http://localhost:8000/query -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"query": "What is the capital of France?"}'

# Streamed answer (Server-Sent Events)
curl -N -X POST http://localhost:8000/query -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"query": "What is the capital of France?", "stream": true}'
```

`scripts/manual/smoke_test_api.sh` runs a fuller scenario. The complete endpoint reference is in [api.md](api.md).

## 6. Tests

```bash
pytest                     # unit tests, no services needed
pytest -m integration      # needs Elasticsearch at ELASTICSEARCH_URL
pytest --cov=src           # coverage (CI requires >= 75%)
```

## Monitoring (optional)

```bash
docker compose -f docker-compose.monitoring.yml up -d
```

Prometheus on http://localhost:9090, Grafana on http://localhost:3000. Metrics are exposed by the API on `/metrics`. See [OBSERVABILITY.md](OBSERVABILITY.md).

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `ConfigurationError: Unsafe configuration` at startup | `FLASK_ENV` is not `development` and a secret is missing or left at its default |
| `/health` returns 503 `degraded` | Elasticsearch is unreachable or the credentials are wrong |
| `IndexDimensionMismatchError` | The index was created for another embedding model: use another `ELASTICSEARCH_INDEX` or delete it (`python scripts/init_elasticsearch.py --delete`) |
| `400 unsafe_url` on URL ingestion | The URL resolves to a private address, or is outside `INGEST_URL_ALLOWLIST` |
| Sessions randomly return 404 with several workers | `REDIS_URL` is not set, so each worker has its own sessions |
