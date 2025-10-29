#!/bin/bash
#
# RAG Agent Startup Script
# This script starts the RAG Automation Agent with all required services
#

set -e  # Exit on error

# Color output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Project directory
PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$PROJECT_DIR"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}RAG Automation Agent - Startup${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Check if .env exists
if [ ! -f .env ]; then
    echo -e "${YELLOW}⚠ No .env file found. Creating from .env.example...${NC}"
    cp .env.example .env
    echo -e "${YELLOW}Please edit .env and add your API keys before continuing.${NC}"
    exit 1
fi

# Check if virtual environment exists
if [ ! -d .venv ]; then
    echo -e "${RED}❌ Virtual environment not found. Please run: python3 -m venv .venv${NC}"
    exit 1
fi

# Activate virtual environment
source .venv/bin/activate

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo -e "${YELLOW}⚠ Docker is not running. Starting Docker...${NC}"
    sudo systemctl start docker
    sleep 2
fi

# Start Elasticsearch
echo -e "${GREEN}🔧 Starting Elasticsearch...${NC}"
docker compose up -d elasticsearch

# Wait for Elasticsearch to be ready
echo -e "${YELLOW}⏳ Waiting for Elasticsearch to be ready...${NC}"
max_attempts=30
attempt=0
while [ $attempt -lt $max_attempts ]; do
    if curl -s -u "elastic:changeme" http://localhost:9200/_cluster/health > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Elasticsearch is ready${NC}"
        break
    fi
    attempt=$((attempt + 1))
    echo -n "."
    sleep 2
done
echo ""

if [ $attempt -eq $max_attempts ]; then
    echo -e "${RED}❌ Elasticsearch failed to start within 60 seconds${NC}"
    exit 1
fi

# Initialize Elasticsearch index
echo -e "${GREEN}🔧 Initializing Elasticsearch index...${NC}"
python scripts/init_elasticsearch.py || true

# Start monitoring stack (optional)
if [ "$1" == "--with-monitoring" ]; then
    echo -e "${GREEN}🔧 Starting Prometheus + Grafana...${NC}"
    docker compose -f docker-compose.monitoring.yml up -d
    echo -e "${GREEN}✓ Monitoring stack started${NC}"
    echo -e "  - Prometheus: http://localhost:9090"
    echo -e "  - Grafana: http://localhost:3000 (admin/admin)"
fi

# Export environment variables for logging
export LOG_LEVEL=${LOG_LEVEL:-INFO}
export LOG_FILE=${LOG_FILE:-logs/app.log}

# Create logs directory
mkdir -p logs

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}✓ All services started successfully!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "Starting Flask application..."
echo -e "API will be available at: ${GREEN}http://localhost:8000${NC}"
echo ""
echo -e "Endpoints:"
echo -e "  - Health: http://localhost:8000/health"
echo -e "  - Metrics: http://localhost:8000/metrics"
echo -e "  - Ingest: POST http://localhost:8000/ingest"
echo -e "  - Query: POST http://localhost:8000/query"
echo ""
echo -e "Press Ctrl+C to stop"
echo ""

# Start Flask application
exec python -m flask --app src.api.app:create_app run --host 0.0.0.0 --port 8000
