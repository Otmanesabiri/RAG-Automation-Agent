# CI/CD Pipeline Documentation

This document describes the Continuous Integration and Continuous Deployment (CI/CD) pipelines configured for the RAG Automation Agent.

## 🔄 Overview

The project uses **GitHub Actions** for automated testing, security scanning, and Docker image building. All workflows are triggered on push to `main` or `develop` branches, and on pull requests.

## 📊 Workflow Badges

Add these badges to your README.md:

```markdown
![Lint](https://github.com/Otmanesabiri/RAG-Automation-Agent/actions/workflows/lint.yml/badge.svg)
![Test](https://github.com/Otmanesabiri/RAG-Automation-Agent/actions/workflows/test.yml/badge.svg)
![Security](https://github.com/Otmanesabiri/RAG-Automation-Agent/actions/workflows/security.yml/badge.svg)
![Docker](https://github.com/Otmanesabiri/RAG-Automation-Agent/actions/workflows/docker.yml/badge.svg)
![Coverage](https://codecov.io/gh/Otmanesabiri/RAG-Automation-Agent/branch/main/graph/badge.svg)
```

---

## 🔍 Workflow Details

### 1. Lint Workflow (`.github/workflows/lint.yml`)

**Triggers:**
- Push to `main` or `develop`
- Pull requests to `main` or `develop`

**What it does:**
- Runs on Python 3.10, 3.11, and 3.12
- **Black**: Code formatter check (PEP 8 compliance)
- **isort**: Import statement sorting verification
- **flake8**: Style guide enforcement (syntax errors, undefined names)
- **mypy**: Static type checking
- **pylint**: Code quality analysis

**Usage:**
```bash
# Run locally before committing
black --check src/ tests/
isort --check-only src/ tests/
flake8 src/ tests/
mypy src/
pylint src/
```

**Auto-fix formatting:**
```bash
black src/ tests/
isort src/ tests/
```

---

### 2. Test Workflow (`.github/workflows/test.yml`)

**Triggers:**
- Push to `main` or `develop`
- Pull requests to `main` or `develop`

**What it does:**
- Runs on Python 3.10, 3.11, and 3.12
- Spins up Elasticsearch service (8.14.0) as a GitHub service container
- Runs pytest with code coverage
- Uploads coverage reports to Codecov
- Comments on PRs with coverage results
- Requires minimum 60% coverage (warns at <80%)

**Local testing:**
```bash
# Start Elasticsearch locally
docker compose up -d elasticsearch

# Run tests with coverage
pytest tests/ \
  --cov=src \
  --cov-report=html \
  --cov-report=term-missing \
  -v

# View coverage report
open htmlcov/index.html
```

**Coverage Thresholds:**
- 🟢 **Green**: ≥80% coverage
- 🟠 **Orange**: 60-79% coverage
- 🔴 **Red**: <60% coverage (CI fails)

---

### 3. Security Workflow (`.github/workflows/security.yml`)

**Triggers:**
- Push to `main` or `develop`
- Pull requests to `main` or `develop`
- **Scheduled**: Every Monday at 9:00 AM UTC

**What it does:**
- **Bandit**: Python security linter (detects common vulnerabilities)
- **Safety**: Scans dependencies for known CVEs
- **pip-audit**: Audits PyPI packages against the OSV database
- **Semgrep**: SAST (Static Application Security Testing)
- **Dependency Review**: Checks for vulnerable dependencies in PRs

**Local security scan:**
```bash
# Install tools
pip install bandit safety pip-audit

# Run scans
bandit -r src/
safety check
pip-audit
```

**Common Issues:**
- SQL injection risks
- Hard-coded secrets
- Insecure random number generation
- Unvalidated redirects

---

### 4. Docker Workflow (`.github/workflows/docker.yml`)

**Triggers:**
- Push to `main` branch
- Git tags matching `v*.*.*` (e.g., `v1.2.3`)
- Manual workflow dispatch
- Pull requests to `main` (build only, no push)

**What it does:**
- Builds multi-architecture Docker images (amd64, arm64)
- Pushes to GitHub Container Registry (`ghcr.io`)
- Uses Docker layer caching for faster builds
- Runs Trivy vulnerability scanner on final image
- Uploads security scan results to GitHub Security tab

**Image Tagging Strategy:**
- `latest` - Latest build from `main` branch
- `v1.2.3` - Semantic version tag
- `v1.2` - Major.minor tag
- `v1` - Major version tag
- `main-abc123` - Branch + commit SHA
- `pr-42` - Pull request number

**Pull the image:**
```bash
docker pull ghcr.io/otmanesabiri/rag-automation-agent:latest
```

**Run the image:**
```bash
docker run -d \
  -p 8000:8000 \
  -e OPENAI_API_KEY=sk-your-key \
  -e ELASTICSEARCH_URL=http://elasticsearch:9200 \
  --name rag-agent \
  ghcr.io/otmanesabiri/rag-automation-agent:latest
```

---

## 🎯 Best Practices for Contributors

### Before Submitting a Pull Request

1. **Format your code:**
   ```bash
   black src/ tests/
   isort src/ tests/
   ```

2. **Run linting:**
   ```bash
   flake8 src/ tests/
   pylint src/
   ```

3. **Run tests locally:**
   ```bash
   pytest tests/ --cov=src -v
   ```

4. **Security scan:**
   ```bash
   bandit -r src/
   ```

5. **Check for secrets:**
   ```bash
   git secrets --scan
   ```

### Writing Tests

- Maintain **≥80% code coverage**
- Use pytest fixtures for reusable test components
- Mock external API calls (OpenAI, Anthropic)
- Test both success and failure scenarios

**Example test structure:**
```python
def test_ingest_endpoint_success(client, mock_elasticsearch):
    """Test successful document ingestion."""
    response = client.post('/ingest', json={
        'documents': [{'content': 'Test', 'filename': 'test.txt'}],
        'source': 'raw'
    })
    assert response.status_code == 200
    assert 'indexed' in response.json
```

### Commit Message Convention

Use **Conventional Commits** format:

```
<type>(<scope>): <subject>

<body>

<footer>
```

**Types:**
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation only
- `style`: Code style (formatting, missing semi-colons)
- `refactor`: Code change that neither fixes a bug nor adds a feature
- `test`: Adding missing tests
- `chore`: Changes to build process or auxiliary tools

**Examples:**
```
feat(api): add batch ingestion endpoint

Implements POST /ingest/batch to process multiple documents
in a single request, improving throughput by 3x.

Closes #42
```

```
fix(embeddings): handle rate limiting from OpenAI

Added exponential backoff with jitter when hitting rate limits.
Retries up to 5 times before failing.

Fixes #58
```

---

## 🚀 Deployment Process

### Manual Deployment

1. **Create a release tag:**
   ```bash
   git tag -a v1.0.0 -m "Release version 1.0.0"
   git push origin v1.0.0
   ```

2. **Docker workflow triggers automatically**
   - Builds and pushes image with version tags
   - Creates GitHub Release (optional)

3. **Deploy to staging/production:**
   ```bash
   docker pull ghcr.io/otmanesabiri/rag-automation-agent:v1.0.0
   docker compose -f docker-compose.prod.yml up -d
   ```

### Automated Deployment (Future - Phase 9)

- **Staging**: Auto-deploy on push to `develop`
- **Production**: Manual approval after successful staging tests
- **Rollback**: One-click rollback to previous version

---

## 📈 Monitoring CI/CD Health

### GitHub Actions Dashboard

View workflow runs: `https://github.com/Otmanesabiri/RAG-Automation-Agent/actions`

### Key Metrics to Monitor

- **Pass Rate**: % of successful workflow runs
- **Build Time**: Average time to complete CI pipeline
- **Flakiness**: Tests that intermittently fail
- **Coverage Trend**: Code coverage over time

### Setting Up Notifications

**Slack Integration:**
1. Go to repository Settings → Webhooks
2. Add Slack webhook URL
3. Configure to notify on workflow failures

**Email Notifications:**
- Automatically sent to commit author on failure
- Configure in GitHub Settings → Notifications

---

## 🔧 Troubleshooting

### "Docker build failed - out of memory"

**Solution**: Reduce parallelism or use multi-stage builds
```dockerfile
# Use multi-stage build
FROM python:3.12-slim AS builder
RUN pip install --user -r requirements.txt

FROM python:3.12-slim
COPY --from=builder /root/.local /root/.local
```

### "Tests failing only in CI, pass locally"

**Common causes:**
- Elasticsearch not ready (increase health check timeout)
- Missing environment variables
- Time-dependent tests (use freezegun)

**Fix:**
```yaml
# Increase Elasticsearch health check retries
options: >-
  --health-retries 30
```

### "Security scan detected vulnerability"

1. Check the CVE details in the workflow logs
2. Update the affected package: `pip install --upgrade package-name`
3. If no fix available, add exception with justification:
   ```python
   # nosec B201 - Flask debug mode disabled in production
   ```

---

## 📚 Additional Resources

- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [Docker Best Practices](https://docs.docker.com/develop/dev-best-practices/)
- [Codecov Documentation](https://docs.codecov.com/)
- [Semgrep Rules](https://semgrep.dev/explore)

---

## 🎓 Quick Reference

| Task | Command |
|------|---------|
| Run all tests | `pytest tests/ -v` |
| Check coverage | `pytest --cov=src --cov-report=html` |
| Format code | `black src/ tests/` |
| Sort imports | `isort src/ tests/` |
| Lint code | `flake8 src/ tests/` |
| Security scan | `bandit -r src/` |
| Build Docker | `docker build -t rag-agent .` |
| Run locally | `./start.sh` |

---

**Last Updated**: October 29, 2025  
**Maintained by**: DevOps Team
