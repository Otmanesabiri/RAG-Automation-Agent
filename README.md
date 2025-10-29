# RAG Automation Agent

The **RAG Automation Agent** is an enterprise-gra## 📚 Documentation & Design Artifacts
- `docs/OBSERVABILITY.md` – Prometheus metrics, Loguru logging, Grafana dashboards.
- `docs/architecture.md` – Vue d'architecture globale du système (à venir).
- `docs/data-flow.md` – Pipeline d'ingestion, indexation et génération (à venir).
- `docs/api.md` – Spécifications REST (à venir).
- `docs/runbook.md` – Procédures d'exploitation et troubleshooting (à venir).atform that combines Retrieval-Augmented Generation (RAG) with autonomous tooling to streamline knowledge work across teams. It automates document ingestion, semantic search, and contextual reasoning to power copilots that answer questions, draft reports, and orchestrate multi-step enterprise workflows.

## 🎯 Vision & Objectives
- Deliver a plug-and-play framework for building secure enterprise copilots.
- Support heterogeneous document sources (PDF, DOCX, HTML, TXT) with metadata-aware retrieval.
- Provide observability, governance, and DevOps pipelines required for production deployments.
- Offer an API-first design that integrates easily with existing business systems.

## 🚀 Key Capabilities
- **Dynamic ingestion pipeline** with modular loaders and intelligent chunking.
- **Vector search powered by Elasticsearch** with pluggable similarity metrics.
- **LLM Orchestration with LangChain**, configurable across OpenAI, Claude, or open-source models.
- **Tool-augmented agent** capable of multi-step reasoning and external tool calls.
- **REST API layer (Flask)** exposing ingestion, query, and health endpoints.
- **Dockerized deployment** with CI/CD via GitHub Actions and full observability stack.

## 🧭 Initial Roadmap
| Phase | Focus | Status |
|-------|-------|--------|
| 1 | Environment setup & architecture baseline | ✅ Complete |
| 2 | Document ingestion & embeddings pipeline | ✅ Complete |
| 3 | Elasticsearch configuration & indexing | ✅ Complete |
| 4 | Retrieval-Augmented Generation flows | ✅ Complete |
| 5 | Flask API layer & security | ✅ Complete |
| 6 | Observability (metrics, logging, tracing) | ✅ Complete |
| 7 | CI/CD pipelines & quality gates | ⏳ Planned |
| 8 | Monitoring dashboards (Grafana) | ⏳ Planned |
| 9 | Staging deployment & validation | ⏳ Planned |
| 10 | Optimization, documentation, and production readiness | ⏳ Planned |

## 🗂️ Repository Structure
```
.
├── configs/            # Configuration files (YAML/JSON) for pipelines and services
├── data/               # Local staging area for raw documents (gitignored)
├── docs/               # Architecture diagrams, API specs, operational runbooks
├── src/                # Application source code (ingestion, retrievers, agents, API)
├── tests/              # Unit and integration test suites (pytest)
├── requirements.txt    # Python dependencies
└── README.md           # Project overview (this document)
```

## 🧪 Target User Scenarios
- **Enterprise knowledge bot** – “Quels sont les points clés du rapport financier Q2 2024 pour la région EMEA ?”
- **Process automation assistant** – “Prépare un plan d’action basé sur les audits ISO 27001 des six derniers mois.”
- **Compliance copilot** – “Quels documents mentionnent la politique de rétention des données et quelles exceptions existent ?”
- **Customer support agent** – “Réponds à la demande du client concernant la procédure de remboursement premium.”
- **Operations analyst** – “Résume les incidents critiques de la semaine dernière et propose des mesures d’atténuation.”

## 🛠️ Quickstart (Local)
1. **Créer un environnement virtuel**
	```bash
	python3 -m venv .venv
	source .venv/bin/activate
	pip install --upgrade pip
	```
2. **Installer les dépendances** (temporairement vide, sera enrichi)
	```bash
	pip install -r requirements.txt
	```
3. **Configurer les variables d’environnement**
	- `OPENAI_API_KEY` ou équivalent pour le fournisseur LLM choisi.
	- `ELASTICSEARCH_URL` pour la base vectorielle.

4. **Lancer Elasticsearch en local**
	```bash
	docker compose up -d elasticsearch
	python scripts/init_elasticsearch.py
	```
	Utilisez la variable `ELASTIC_PASSWORD` définie dans `.env` (par défaut `changeme`).

## 📚 Documentation & Design Artifacts
- `docs/architecture.md` – Vue d’architecture globale du système (à venir).
- `docs/data-flow.md` – Pipeline d’ingestion, indexation et génération (à venir).
- `docs/api.md` – Spécifications REST (à venir).
- `docs/runbook.md` – Procédures d’exploitation et troubleshooting (à venir).

## 🤝 Contribution & Governance
- Configurer un dépôt GitHub et activer les protections de branches (`main`, `develop`).
- Utiliser GitHub Projects ou Issues pour suivre l’avancement des phases.
- Définir des revues de code obligatoires et automatiser les contrôles via GitHub Actions.

## 📬 Contact
Pour toute question, ouvrez une issue ou contactez l’équipe plateforme IA.
# RAG-Automation-Agent
