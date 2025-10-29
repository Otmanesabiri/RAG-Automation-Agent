#!/bin/bash
#
# Quick test script for RAG Agent
#

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

API_KEY="test-key-123"
BASE_URL="http://localhost:8000"

echo -e "${GREEN}Testing RAG Automation Agent API${NC}"
echo ""

# Test 1: Health Check
echo -e "${YELLOW}Test 1: Health Check${NC}"
response=$(curl -s "$BASE_URL/health")
if echo "$response" | grep -q "ok"; then
    echo -e "${GREEN}✓ Health check passed${NC}"
    echo "$response" | python3 -m json.tool
else
    echo -e "${RED}✗ Health check failed${NC}"
    echo "$response"
    exit 1
fi
echo ""

# Test 2: Ingest Document
echo -e "${YELLOW}Test 2: Ingest Document${NC}"
ingest_response=$(curl -s -X POST "$BASE_URL/ingest" \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "documents": [
      {
        "content": "The RAG Automation Agent is a production-ready enterprise copilot platform. It supports PDF, DOCX, HTML, and TXT document ingestion with semantic search powered by Elasticsearch. The system includes authentication, rate limiting, and comprehensive observability with Prometheus and Grafana.",
        "filename": "intro.txt",
        "metadata": {
          "category": "documentation",
          "version": "1.0",
          "author": "AI Team"
        }
      }
    ],
    "source": "raw"
  }')

if echo "$ingest_response" | grep -q "indexed"; then
    echo -e "${GREEN}✓ Document ingestion passed${NC}"
    echo "$ingest_response" | python3 -m json.tool
else
    echo -e "${RED}✗ Document ingestion failed${NC}"
    echo "$ingest_response"
fi
echo ""

# Wait for indexing
echo -e "${YELLOW}Waiting 2 seconds for indexing...${NC}"
sleep 2

# Test 3: Query RAG
echo -e "${YELLOW}Test 3: Query RAG System${NC}"
query_response=$(curl -s -X POST "$BASE_URL/query" \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What document formats does the RAG Agent support?",
    "top_k": 3
  }')

if echo "$query_response" | grep -q "answer"; then
    echo -e "${GREEN}✓ RAG query passed${NC}"
    echo "$query_response" | python3 -m json.tool
else
    echo -e "${RED}✗ RAG query failed${NC}"
    echo "$query_response"
fi
echo ""

# Test 4: Metrics
echo -e "${YELLOW}Test 4: Prometheus Metrics${NC}"
metrics=$(curl -s "$BASE_URL/metrics" | grep "rag_agent")
if [ -n "$metrics" ]; then
    echo -e "${GREEN}✓ Metrics endpoint working${NC}"
    echo "$metrics" | head -10
else
    echo -e "${RED}✗ Metrics endpoint failed${NC}"
fi
echo ""

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}All tests completed!${NC}"
echo -e "${GREEN}========================================${NC}"
