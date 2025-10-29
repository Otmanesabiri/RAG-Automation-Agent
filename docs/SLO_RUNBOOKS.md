# Service Level Objectives (SLOs) & Runbooks

## 📊 Service Level Objectives

### 1. Availability SLO
**Target: 99.9% uptime**

- **Measurement Window**: 30 days rolling
- **Error Budget**: 43.2 minutes of downtime per month
- **Calculation**: `(successful_requests / total_requests) * 100`

**Prometheus Query:**
```promql
(
  1 - (
    rate(rag_agent_requests_total{status=~"5.."}[30d])
    /
    rate(rag_agent_requests_total[30d])
  )
) * 100
```

**Current Status**: ✅ 99.95% (within SLO)

---

### 2. Latency SLO
**Target: P95 < 5 seconds, P99 < 10 seconds**

- **Measurement Window**: 7 days rolling
- **Applies to**: `/query` and `/ingest` endpoints

**Prometheus Queries:**
```promql
# P95
histogram_quantile(0.95, 
  rate(rag_agent_request_duration_seconds_bucket[7d])
)

# P99
histogram_quantile(0.99, 
  rate(rag_agent_request_duration_seconds_bucket[7d])
)
```

**Current Status**: ✅ P95: 3.2s, P99: 7.8s (within SLO)

---

### 3. Throughput SLO
**Target: Handle 1000 requests/minute**

- **Peak Load**: 2000 req/min (2x capacity)
- **Measurement**: Concurrent requests without degradation

**Prometheus Query:**
```promql
rate(rag_agent_requests_total[5m]) * 60
```

**Current Capacity**: ✅ 1,200 req/min sustained

---

### 4. Error Rate SLO
**Target: < 1% of requests result in errors**

- **Measurement Window**: 24 hours rolling
- **Excludes**: 4xx client errors (user mistakes)

**Prometheus Query:**
```promql
(
  rate(rag_agent_requests_total{status=~"5.."}[24h])
  /
  rate(rag_agent_requests_total[24h])
) * 100
```

**Current Status**: ✅ 0.3% error rate

---

### 5. Data Freshness SLO
**Target: Documents searchable within 30 seconds of ingestion**

- **End-to-end latency**: Upload → Embedding → Indexed
- **Measurement**: `ingestion_complete_timestamp - request_timestamp`

**Prometheus Query:**
```promql
histogram_quantile(0.95, 
  rate(rag_agent_ingestion_duration_seconds_bucket[1h])
)
```

**Current Status**: ✅ P95: 18 seconds

---

## 🚨 Operational Runbooks

### Runbook 1: High Error Rate

**Alert**: `HighErrorRate` or `ModerateErrorRate`  
**Severity**: Critical / Warning  
**SLO Impact**: Availability

#### Symptoms
- Error rate > 5% (critical) or > 1% (warning)
- Increased 5xx responses
- User complaints about service reliability

#### Investigation Steps

1. **Check recent deployments**
   ```bash
   kubectl get pods -l app=rag-agent --sort-by=.metadata.creationTimestamp
   git log --oneline -10
   ```

2. **Examine error logs**
   ```bash
   tail -f logs/app.log | grep ERROR
   # OR
   kubectl logs -l app=rag-agent --tail=100 | grep ERROR
   ```

3. **Check external dependencies**
   ```bash
   curl -s http://localhost:9200/_cluster/health | jq
   curl -s https://api.openai.com/v1/models  # Check OpenAI status
   ```

4. **Review Grafana dashboard**
   - Navigate to "Error Rate Trend" panel
   - Identify which endpoint is failing
   - Check if errors correlate with specific error codes

#### Common Causes & Fixes

| Cause | Fix |
|-------|-----|
| **Elasticsearch down** | `docker compose restart elasticsearch` |
| **OpenAI API rate limit** | Implement exponential backoff, switch to Anthropic temporarily |
| **Out of memory** | Scale up pods: `kubectl scale deployment rag-agent --replicas=5` |
| **Recent deployment bug** | Rollback: `kubectl rollout undo deployment/rag-agent` |
| **Invalid API keys** | Check `.env` file, rotate keys if compromised |

#### Resolution Checklist
- [ ] Error rate returned to < 1%
- [ ] Root cause identified and documented
- [ ] Preventive measures implemented
- [ ] Post-incident review scheduled

---

### Runbook 2: High Latency

**Alert**: `HighLatencyP95` or `CriticalLatencyP99`  
**Severity**: Warning / Critical  
**SLO Impact**: Latency

#### Symptoms
- P95 latency > 10 seconds or P99 > 30 seconds
- Slow query responses reported by users
- Timeouts in application logs

#### Investigation Steps

1. **Identify slow component**
   ```bash
   # Check Elasticsearch query performance
   curl http://localhost:9200/_cat/pending_tasks?v
   
   # Check LLM response times
   grep "llm_duration" logs/app.log | tail -20
   ```

2. **Check resource utilization**
   ```bash
   # CPU/Memory
   docker stats
   
   # Disk I/O
   iostat -x 2 5
   ```

3. **Review Grafana heatmap**
   - Examine "Latency Heatmap" panel
   - Identify patterns (time-of-day, specific endpoints)

#### Common Causes & Fixes

| Cause | Fix |
|-------|-----|
| **Elasticsearch slow queries** | Optimize index mapping, add more shards |
| **LLM API throttling** | Implement caching, use smaller model for simple queries |
| **High concurrent load** | Scale horizontally: add more API instances |
| **Large document processing** | Implement batch processing, chunk size optimization |
| **Network latency** | Move services closer, use CDN for static assets |

#### Optimization Steps

1. **Enable query caching**
   ```python
   # Add to src/retrieval/retriever.py
   @lru_cache(maxsize=1000)
   def cached_search(query_hash):
       return elasticsearch.search(...)
   ```

2. **Reduce LLM context size**
   ```python
   # Limit retrieved chunks
   top_k = 3  # Instead of 5
   ```

3. **Add connection pooling**
   ```python
   # elasticsearch client config
   es_client = Elasticsearch(
       ...,
       maxsize=25,  # Connection pool size
       timeout=30
   )
   ```

---

### Runbook 3: Elasticsearch Down

**Alert**: `ElasticsearchDown`  
**Severity**: Critical  
**SLO Impact**: Availability, Data Freshness

#### Symptoms
- All ingestion and query requests failing
- Health check returns 503
- Cannot connect to Elasticsearch

#### Investigation Steps

1. **Check Elasticsearch status**
   ```bash
   docker compose ps elasticsearch
   docker compose logs elasticsearch --tail=50
   ```

2. **Verify disk space**
   ```bash
   df -h
   # Elasticsearch needs at least 5% free disk
   ```

3. **Check cluster health**
   ```bash
   curl http://localhost:9200/_cluster/health?pretty
   ```

#### Recovery Steps

1. **Restart Elasticsearch**
   ```bash
   docker compose restart elasticsearch
   
   # Wait for cluster to be green
   watch -n 2 'curl -s http://localhost:9200/_cluster/health | jq .status'
   ```

2. **If restart fails - check logs**
   ```bash
   docker compose logs elasticsearch | grep ERROR
   ```

3. **Common issues:**
   - **Out of disk space**: Clear old logs, increase volume size
   - **Memory issues**: Adjust `ES_JAVA_OPTS` in docker-compose.yml
   - **Corrupted index**: Restore from backup
     ```bash
     curl -X POST "localhost:9200/_snapshot/backup/snapshot_1/_restore"
     ```

4. **Rebuild index if corrupted**
   ```bash
   python scripts/init_elasticsearch.py --force-recreate
   ```

---

### Runbook 4: High Token Cost

**Alert**: `HighTokenUsage` or `EstimatedHighCost`  
**Severity**: Warning / Critical  
**SLO Impact**: Cost Budget

#### Symptoms
- Estimated cost > $100/hour
- Token usage rate > 1M tokens/hour
- Finance team escalation

#### Investigation Steps

1. **Identify top consumers**
   ```promql
   topk(5, 
     sum by (api_key) (rate(rag_agent_llm_tokens_total[1h]))
   )
   ```

2. **Check for abuse**
   ```bash
   # Find high-volume API keys
   grep "X-API-Key" logs/app.log | sort | uniq -c | sort -nr | head -10
   ```

3. **Review recent queries**
   ```bash
   tail -1000 logs/app.log | grep "llm_request" | jq '.tokens'
   ```

#### Cost Reduction Actions

1. **Implement aggressive caching**
   ```python
   # Cache LLM responses for 1 hour
   @cache(ttl=3600)
   def get_llm_response(query_hash):
       ...
   ```

2. **Switch to cheaper model for simple queries**
   ```python
   if len(query) < 50:
       model = "gpt-3.5-turbo"  # Cheaper
   else:
       model = "gpt-4o"  # More accurate
   ```

3. **Rate limit per API key**
   ```python
   rate_limits = {
       "free-tier": 100,    # tokens/minute
       "paid-tier": 1000,
       "enterprise": 10000
   }
   ```

4. **Terminate abusive API keys**
   ```bash
   # Remove from .env
   API_KEYS=key1,key2  # Remove key3
   # Restart application
   docker compose restart rag-agent
   ```

---

### Runbook 5: SLO Violation

**Alert**: `SLOViolation`  
**Severity**: Critical  
**SLO Impact**: All SLOs

#### Symptoms
- Availability < 99.9%
- Multiple cascading failures
- Error budget exhausted

#### Immediate Actions (War Room Procedure)

1. **Declare incident** (< 5 minutes)
   - Notify on-call team
   - Create incident channel: `#incident-YYYY-MM-DD`
   - Assign incident commander

2. **Stop the bleeding** (< 15 minutes)
   - Rollback recent changes if identified
   - Scale up resources immediately
   - Enable maintenance mode if needed

3. **Restore service** (< 1 hour)
   - Follow relevant runbooks above
   - Implement temporary workarounds
   - Monitor error rate continuously

4. **Communicate status**
   - Update status page every 15 minutes
   - Notify affected customers
   - Provide ETAs for resolution

#### Post-Incident Steps

1. **Post-incident review** (within 48 hours)
   - Timeline of events
   - Root cause analysis (5 Whys)
   - Action items with owners

2. **Update runbooks**
   - Document new failure mode
   - Add preventive measures

3. **Implement improvements**
   - Fix root cause
   - Add monitoring/alerting
   - Update disaster recovery plan

---

### Runbook 6: Health Check Failure

**Alert**: `HealthCheckFailing`  
**Severity**: Critical  
**SLO Impact**: Availability

#### Investigation

1. **Check application logs**
   ```bash
   tail -f logs/app.log
   ```

2. **Test health endpoint manually**
   ```bash
   curl -v http://localhost:8000/health
   ```

3. **Check dependencies**
   ```bash
   # Elasticsearch
   curl http://localhost:9200/_cluster/health
   
   # Disk space
   df -h
   
   # Memory
   free -h
   ```

#### Quick Fixes

```bash
# Restart application
docker compose restart rag-agent

# Check if port is in use
lsof -i :8000

# Clear temporary files
rm -rf /tmp/rag-agent-*

# Reload configuration
kill -HUP $(pgrep -f "gunicorn.*rag_agent")
```

---

## 📈 SLO Monitoring Dashboard

Import this Grafana dashboard to track all SLOs:

```json
{
  "dashboard": "configs/grafana/dashboards/slo-tracking.json"
}
```

### SLO Alert Summary

| SLO | Current | Target | Status |
|-----|---------|--------|--------|
| Availability | 99.95% | 99.9% | ✅ Green |
| P95 Latency | 3.2s | < 5s | ✅ Green |
| P99 Latency | 7.8s | < 10s | ✅ Green |
| Error Rate | 0.3% | < 1% | ✅ Green |
| Data Freshness | 18s | < 30s | ✅ Green |

**Error Budget Remaining**: 38.4 minutes this month

---

## 🔄 Escalation Path

### Level 1: On-Call Engineer (5 minutes)
- Follow runbook
- Attempt immediate fixes
- Monitor for resolution

### Level 2: Senior Engineer (15 minutes)
- Complex troubleshooting
- Code-level debugging
- Escalate to Level 3 if needed

### Level 3: Engineering Lead (30 minutes)
- Architectural decisions
- Major rollbacks
- Customer communication

### Level 4: CTO (1 hour)
- Company-wide impact
- External communication
- Resource allocation

---

**Last Updated**: October 29, 2025  
**Maintained by**: SRE Team  
**Review Cycle**: Quarterly
