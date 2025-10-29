# Observability Guide

This document describes the monitoring, logging, and metrics capabilities of the RAG Automation Agent.

## Table of Contents

1. [Prometheus Metrics](#prometheus-metrics)
2. [Structured Logging](#structured-logging)
3. [Health Checks](#health-checks)
4. [Grafana Dashboards](#grafana-dashboards)

---

## Prometheus Metrics

The application exposes Prometheus-compatible metrics at `/metrics` endpoint.

### Available Metrics

#### Request Metrics

```
rag_agent_requests_total{method, endpoint, status}
```
- **Type**: Counter
- **Description**: Total number of API requests
- **Labels**:
  - `method`: HTTP method (GET, POST)
  - `endpoint`: Route name
  - `status`: HTTP status code

```
rag_agent_request_duration_seconds{method, endpoint}
```
- **Type**: Histogram
- **Description**: Request latency distribution
- **Buckets**: Default Prometheus histogram buckets

#### Ingestion Metrics

```
rag_agent_ingestion_documents_total
```
- **Type**: Counter
- **Description**: Total documents ingested into the system

#### Query Metrics

```
rag_agent_queries_total
```
- **Type**: Counter
- **Description**: Total RAG queries executed

#### Embedding Metrics

```
rag_agent_embedding_tokens_total
```
- **Type**: Counter
- **Description**: Total tokens embedded (for tracking costs)

### Example Queries

**Request rate by endpoint:**
```promql
rate(rag_agent_requests_total[5m])
```

**P95 latency:**
```promql
histogram_quantile(0.95, rate(rag_agent_request_duration_seconds_bucket[5m]))
```

**Error rate:**
```promql
rate(rag_agent_requests_total{status=~"5.."}[5m])
```

---

## Structured Logging

The application uses **Loguru** for structured, contextual logging.

### Configuration

Set via environment variables:

```bash
LOG_LEVEL=INFO          # DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_FILE=logs/app.log   # Optional file output with rotation
```

### Log Format

**Console (development):**
```
2024-01-15 14:32:10 | INFO     | src.api.routes:query_documents:85 - Executing RAG query: What is...
```

**File (production):**
```
2024-01-15 14:32:10 | INFO     | src.api.routes:query_documents:85 - Executing RAG query: What is...
```

### Log Rotation

File logs automatically rotate when:
- File size exceeds **100 MB**
- Retention period: **30 days**
- Compression: **ZIP format**

### Example Log Statements

```python
from src.observability import get_logger

logger = get_logger(__name__)

logger.info(f"User {user_id} ingested {doc_count} documents")
logger.warning(f"Slow query detected: {latency}s")
logger.error(f"Elasticsearch connection failed: {error}")
```

---

## Health Checks

### `/health` Endpoint

**Response:**
```json
{
  "status": "ok",
  "version": "0.1.0",
  "elasticsearch": "green"
}
```

**Elasticsearch Status:**
- `green`: All primary and replica shards are active
- `yellow`: All primary shards are active, but not all replicas
- `red`: Not all primary shards are active
- `unknown`: Connection failed

### Readiness Probe

Use in Kubernetes:
```yaml
readinessProbe:
  httpGet:
    path: /health
    port: 8000
  initialDelaySeconds: 10
  periodSeconds: 5
```

---

## Grafana Dashboards

### Recommended Panels

1. **Request Rate**
   - Metric: `rate(rag_agent_requests_total[5m])`
   - Visualization: Time series graph

2. **Latency Percentiles**
   - Metric: `histogram_quantile(0.50|0.95|0.99, rate(rag_agent_request_duration_seconds_bucket[5m]))`
   - Visualization: Multi-line graph

3. **Error Rate**
   - Metric: `rate(rag_agent_requests_total{status=~"5.."}[5m])`
   - Visualization: Time series with alert threshold

4. **Ingestion Throughput**
   - Metric: `rate(rag_agent_ingestion_documents_total[5m])`
   - Visualization: Single stat

5. **Query Volume**
   - Metric: `rate(rag_agent_queries_total[5m])`
   - Visualization: Single stat

### Alerting Rules

**High Error Rate:**
```yaml
- alert: HighErrorRate
  expr: rate(rag_agent_requests_total{status=~"5.."}[5m]) > 0.05
  for: 5m
  annotations:
    summary: "High error rate detected"
```

**Slow Queries:**
```yaml
- alert: SlowQueries
  expr: histogram_quantile(0.95, rate(rag_agent_request_duration_seconds_bucket[5m])) > 10
  for: 10m
  annotations:
    summary: "P95 latency exceeds 10s"
```

---

## Integration Example

### Docker Compose with Monitoring Stack

Add to `docker-compose.yml`:

```yaml
services:
  prometheus:
    image: prom/prometheus:latest
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus-data:/prometheus

  grafana:
    image: grafana/grafana:latest
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
    volumes:
      - grafana-data:/var/lib/grafana

volumes:
  prometheus-data:
  grafana-data:
```

### Prometheus Configuration (`prometheus.yml`)

```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'rag-agent'
    static_configs:
      - targets: ['host.docker.internal:8000']
    metrics_path: '/metrics'
```

---

## Best Practices

1. **Metrics Cardinality**: Avoid high-cardinality labels (e.g., user IDs, document content)
2. **Log Sampling**: In production, consider sampling verbose DEBUG logs
3. **Alerting Thresholds**: Tune based on baseline traffic patterns
4. **Dashboard Refresh**: Set to 30s-1m for real-time monitoring
5. **Retention**: Configure Prometheus retention (default 15 days) based on storage capacity

---

## Troubleshooting

### Metrics not appearing

1. Check `/metrics` endpoint returns data:
   ```bash
   curl http://localhost:8000/metrics
   ```

2. Verify Prometheus scraping:
   ```bash
   curl http://localhost:9090/api/v1/targets
   ```

### Log file not created

1. Check write permissions:
   ```bash
   mkdir -p logs
   chmod 755 logs
   ```

2. Verify `LOG_FILE` environment variable is set

### Elasticsearch health unknown

1. Test connection:
   ```bash
   curl http://localhost:9200/_cluster/health
   ```

2. Check credentials in `.env` file
