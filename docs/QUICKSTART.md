# RAG Agent - Quickstart Guide

This guide will get the RAG Automation Agent running on your local machine in under 10 minutes.

## Prerequisites

- **Python 3.9+** (check with `python --version`)
- **Docker & Docker Compose** (for Elasticsearch and monitoring)
- **Git** (for cloning the repository)
- **OpenAI API Key** (for embeddings and LLM)

---

## Step 1: Clone and Setup Environment

```bash
# Navigate to project directory
cd "/home/red/Documents/GLSID3/preparation stage/AI_Agent"

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Upgrade pip
pip install --upgrade pip

# Install dependencies
pip install -r requirements.txt
```

**Expected time**: 2-3 minutes

---

## Step 2: Configure Environment Variables

```bash
# Copy example configuration
cp .env.example .env

# Edit .env and add your credentials
nano .env  # or use your preferred editor
```

**Required variables**:
```bash
OPENAI_API_KEY=sk-...  # Your OpenAI API key

# Elasticsearch (defaults are fine for local development)
ELASTICSEARCH_URL=http://localhost:9200
ELASTICSEARCH_USERNAME=elastic
ELASTICSEARCH_PASSWORD=changeme
ELASTICSEARCH_INDEX=rag_documents

# Authentication (set your own keys)
AUTH_ENABLED=true
API_KEYS=dev-key-123,test-key-456
```

---

## Step 3: Start Infrastructure Services

```bash
# Start Elasticsearch
docker compose up -d elasticsearch

# Wait for Elasticsearch to be ready (30-60 seconds)
docker compose logs -f elasticsearch
# Press Ctrl+C when you see "Cluster health status changed from [YELLOW] to [GREEN]"

# Initialize Elasticsearch index
python scripts/init_elasticsearch.py
```

**Expected output**:
```
Index 'rag_documents' created successfully
```

**Optional - Start Monitoring Stack**:
```bash
# Start Prometheus and Grafana
docker compose -f docker-compose.monitoring.yml up -d

# Access Grafana at http://localhost:3000 (admin/admin)
```

---

## Step 4: Start the RAG Agent API

```bash
# Development mode with auto-reload
export FLASK_ENV=development
python -m flask --app src.api.app:create_app run --host 0.0.0.0 --port 8000

# Or production mode with Gunicorn (better performance)
gunicorn -w 4 -b 0.0.0.0:8000 'src.api.app:create_app()'
```

**Expected output**:
```
 * Running on http://0.0.0.0:8000
```

---

## Step 5: Test the API

### Health Check

```bash
curl http://localhost:8000/health
```

**Expected response**:
```json
{
  "status": "ok",
  "version": "0.1.0",
  "elasticsearch": "green"
}
```

### Ingest a Document

```bash
curl -X POST http://localhost:8000/ingest \
  -H "X-API-Key: dev-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "documents": [
      {
        "content": "The RAG Automation Agent is a production-ready system for building enterprise copilots. It supports PDF, DOCX, HTML, and TXT document ingestion with semantic search powered by Elasticsearch.",
        "filename": "intro.txt",
        "metadata": {
          "category": "documentation",
          "version": "1.0"
        }
      }
    ],
    "source": "raw"
  }'
```

**Expected response**:
```json
{
  "status": "indexed",
  "documents": [
    {
      "source": "intro.txt",
      "chunks": 1,
      "indexed_ids": ["abc123..."],
      "mime_type": "text/plain"
    }
  ]
}
```

### Query the Documents

```bash
curl -X POST http://localhost:8000/query \
  -H "X-API-Key: dev-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What document formats does the RAG Agent support?",
    "top_k": 3
  }'
```

**Expected response**:
```json
{
  "answer": "The RAG Automation Agent supports PDF, DOCX, HTML, and TXT document formats for ingestion.",
  "sources": [
    {
      "content": "The RAG Automation Agent is a production-ready system...",
      "metadata": {
        "category": "documentation",
        "version": "1.0"
      },
      "score": 0.85
    }
  ],
  "metadata": {
    "session_id": "sess_..."
  }
}
```

---

## Step 6: View Metrics and Logs

### Prometheus Metrics

```bash
curl http://localhost:8000/metrics
```

You should see metrics like:
```
rag_agent_requests_total{method="POST",endpoint="query_documents",status="200"} 1.0
rag_agent_queries_total 1.0
rag_agent_ingestion_documents_total 1.0
```

### Structured Logs

```bash
# If LOG_FILE is configured in .env
tail -f logs/app.log

# Or check console output where Flask is running
```

### Grafana Dashboard

1. Open http://localhost:3000
2. Login: `admin` / `admin`
3. Navigate to Dashboards → RAG Agent - API Performance
4. View real-time metrics and charts

---

## Troubleshooting

### Elasticsearch Connection Errors

**Error**: `ConnectionError: Connection refused`

**Solution**:
```bash
# Check if Elasticsearch is running
docker compose ps

# View logs
docker compose logs elasticsearch

# Restart if needed
docker compose restart elasticsearch
```

### API Key Authentication Failed

**Error**: `401 Unauthorized - Missing or invalid API key`

**Solution**:
```bash
# Verify API key in .env file
grep API_KEYS .env

# Ensure X-API-Key header matches
curl -H "X-API-Key: dev-key-123" ...
```

### Rate Limiting Errors

**Error**: `429 Too Many Requests - Rate limit exceeded`

**Solution**:
```bash
# Increase rate limit in .env
RATE_LIMIT_RPM=120  # Default is 60 requests per minute

# Or disable auth for testing
AUTH_ENABLED=false
```

### Import Errors

**Error**: `ModuleNotFoundError: No module named 'flask'`

**Solution**:
```bash
# Ensure virtual environment is activated
source .venv/bin/activate

# Reinstall dependencies
pip install -r requirements.txt
```

---

## Next Steps

### Ingest Real Documents

```bash
# PDF from URL
curl -X POST http://localhost:8000/ingest \
  -H "X-API-Key: dev-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "documents": [
      {
        "url": "https://arxiv.org/pdf/2103.00020.pdf",
        "metadata": {"title": "REALM Paper"}
      }
    ],
    "source": "url"
  }'

# Local PDF file (base64 encoded)
curl -X POST http://localhost:8000/ingest \
  -H "X-API-Key: dev-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "documents": [
      {
        "content": "'$(base64 -w 0 document.pdf)'",
        "filename": "document.pdf",
        "base64_encoded": true
      }
    ],
    "source": "raw"
  }'
```

### Build a Frontend

The API is ready for integration with web or mobile frontends:

```javascript
// Example: React component
const response = await fetch('http://localhost:8000/query', {
  method: 'POST',
  headers: {
    'X-API-Key': 'dev-key-123',
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    query: userInput,
    top_k: 5
  })
});

const data = await response.json();
console.log(data.answer, data.sources);
```

### Deploy to Production

See [docs/DEPLOYMENT.md](./DEPLOYMENT.md) (coming in Phase 9) for production deployment instructions with:
- Managed Elasticsearch (AWS OpenSearch, Elastic Cloud)
- Container orchestration (Kubernetes, ECS)
- Secrets management (AWS Secrets Manager, HashiCorp Vault)
- SSL/TLS certificates
- Horizontal scaling

---

## API Endpoints Reference

| Endpoint | Method | Auth Required | Purpose |
|----------|--------|---------------|---------|
| `/health` | GET | No | Health check with Elasticsearch status |
| `/ingest` | POST | Yes | Ingest and index documents |
| `/query` | POST | Yes | Execute RAG query with retrieval + generation |
| `/sessions` | POST | No | Create new conversation session |
| `/sessions/<id>` | GET | No | Retrieve session history |
| `/metrics` | GET | No | Prometheus metrics export |

---

## Configuration Reference

See [.env.example](.env.example) for all available environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | - | OpenAI API key (required) |
| `ANTHROPIC_API_KEY` | - | Anthropic API key (optional, for Claude) |
| `ELASTICSEARCH_URL` | `http://localhost:9200` | Elasticsearch endpoint |
| `ELASTICSEARCH_INDEX` | `rag_documents` | Index name for document storage |
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `LOG_FILE` | - | Optional log file path with rotation |
| `AUTH_ENABLED` | `true` | Enable/disable API key authentication |
| `API_KEYS` | - | Comma-separated list of valid API keys |
| `RATE_LIMIT_RPM` | `60` | Requests per minute per API key/IP |

---

## Support

- **Documentation**: [docs/](./docs/)
- **Issues**: [GitHub Issues](https://github.com/your-org/rag-agent/issues)
- **Observability Guide**: [docs/OBSERVABILITY.md](./docs/OBSERVABILITY.md)

---

**Congratulations!** 🎉 Your RAG Automation Agent is now running. Start ingesting documents and building intelligent copilots!
