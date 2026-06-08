.PHONY: help install install-ui verify verify-offline verify-m3 run run-dev ui-dev ui-build test clean services services-down services-logs check-env

help:
	@echo "Continuum — Agentic SDLC Pipeline"
	@echo ""
	@echo "Commands:"
	@echo "  make install        — Install Python dependencies"
	@echo "  make install-ui     — Install React UI dependencies"
	@echo "  make check-env      — Show which env vars are set (live vs offline)"
	@echo "  make services       — Start Neo4j + Postgres (docker-compose, detached)"
	@echo "  make services-down  — Stop the docker-compose services"
	@echo "  make services-logs  — Tail docker-compose logs"
	@echo "  make verify         — Run full verification (lint, type, test, offline)"
	@echo "  make verify-offline — Run 11/11 + 3/3 offline checks only
  make verify-m3      — Run M3 learning-lift demo (6/6)"
	@echo "  make run            — Build UI + start API (serves ui/dist at /)"
	@echo "  make run-dev        — Start API only (use 'make ui-dev' in another terminal)"
	@echo "  make ui-dev         — Start Vite dev server on :5173 (proxies API)"
	@echo "  make ui-build       — Build React UI into ui/dist/"
	@echo "  make test           — Run test suite"
	@echo "  make clean          — Remove __pycache__, .pytest_cache, ui/dist"

install:
	pip install -r requirements.txt

install-ui:
	cd ui && npm install

check-env:
	@echo "Checking Continuum environment variables..."
	@python -c "\
import os; \
live = [v for v in ['AZURE_OPENAI_API_KEY','AZURE_OPENAI_ENDPOINT','AZURE_DEPLOYMENT_STRONG','AZURE_DEVOPS_TOKEN','AZURE_DEVOPS_ORG','AZURE_DEVOPS_PROJECT','NEO4J_URI','NEO4J_PASSWORD','POSTGRES_URI'] if os.getenv(v)]; \
missing = [v for v in ['AZURE_OPENAI_API_KEY','AZURE_OPENAI_ENDPOINT','AZURE_DEPLOYMENT_STRONG','AZURE_DEVOPS_TOKEN','AZURE_DEVOPS_ORG','AZURE_DEVOPS_PROJECT'] if not os.getenv(v)]; \
print('  Set   :', ', '.join(live) if live else '(none)'); \
print('  Unset :', ', '.join(missing) if missing else '(none)'); \
print(); \
print('  Azure AI:', 'LIVE' if os.getenv('AZURE_OPENAI_API_KEY') else 'offline (stub)'); \
print('  ADO:     ', 'LIVE' if os.getenv('AZURE_DEVOPS_TOKEN') else 'offline (stub)'); \
print('  Neo4j:   ', 'configured' if os.getenv('NEO4J_URI') else 'docker-compose default'); \
print('  Postgres:', 'configured' if os.getenv('POSTGRES_URI') else 'docker-compose default'); \
"

services:
	docker-compose up -d neo4j postgres

services-down:
	docker-compose down

services-logs:
	docker-compose logs -f --tail=100

verify: verify-offline
	@echo "Running lint..."
	ruff check .
	@echo "Running type check..."
	mypy . --ignore-missing-imports
	@echo "Running unit tests..."
	pytest tests/ -v
	@echo "✓ All checks passed"

verify-offline:
	@echo "Running offline verification (11/11 + 3/3)..."
	python scripts/verify_agent_core.py
	python scripts/verify_m0_loop.py

verify-m3:
	@echo "Running M3 learning-lift verification (6/6)..."
	python scripts/verify_m3_learning.py

ui-build:
	cd ui && npm run build

run: ui-build
	python -m uvicorn api.main:app --host 0.0.0.0 --port 8000

run-dev:
	python -m uvicorn api.main:app --reload --port 8000

ui-dev:
	cd ui && npm run dev

test:
	pytest tests/ -v --cov=orchestrator --cov=agents

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf ui/dist
