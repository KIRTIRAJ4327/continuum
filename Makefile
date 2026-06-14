.PHONY: help install install-ui verify verify-offline verify-m3 verify-m4 verify-m5 verify-m6 verify-m7 verify-m8 verify-m9 verify-m10 eval-baseline eval-ci eval-report evo-observe evo-propose evo-promote run run-dev ui-dev ui-build test clean services services-down services-logs check-env

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
	@echo "  make verify-offline — Run 11/11 + 3/3 offline checks only"
	@echo "  make verify-m3      — Run M3 learning-lift demo (6/6)"
	@echo "  make verify-m4      — Run M4 CI gate (must exit 0)"
	@echo "  make verify-m5      — Run M5 Evolution Agent demo (6/6)"
	@echo "  make verify-m6      — Run M6 Work Queue + Evidence Stack demo (6/6)"
	@echo "  make verify-m7      — Run M7 Mapping Fidelity / Scope-Guard demo (2/2)"
	@echo "  make verify-m8      — Run M8 Two-Layer Repo Split demo (3/3)"
	@echo "  make verify-m9      — Run M9 MAF Harness Pilot demo (6/6)"
	@echo "  make verify-m10     — Run M10 ASSERT / Rubric Eval demo (6/6)"
	@echo "  make evo-observe    — Print current failure patterns from event history"
	@echo "  make evo-propose    — Generate + eval a proposal, write to pending/"
	@echo "  make evo-promote    — List pending proposals; apply by ID"
	@echo "  make eval-baseline  — Run full suite (20 cases), write baseline.json"
	@echo "  make eval-ci        — Fast CI check (5 cases, block on regression)"
	@echo "  make eval-report    — Full pass^k report (k=5, all 20 cases)"
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

verify-m4:
	@echo "Running M4 CI gate..."
	python evals/ci_gate.py

verify-m5:
	@echo "Running M5 Evolution Agent verification (6/6)..."
	python scripts/verify_m5_evolution.py

verify-m6:
	@echo "Running M6 Work Queue + Evidence Stack verification (6/6)..."
	python scripts/verify_m6_workqueue.py

verify-m7:
	@echo "Running M7 Mapping Fidelity / Scope-Guard verification (2/2)..."
	python scripts/verify_m7_scope_guard.py

verify-m8:
	@echo "Running M8 Two-Layer Repo Split verification (3/3)..."
	python scripts/verify_m8_repo_split.py

verify-m9:
	@echo "Running M9 MAF Harness Pilot verification (6/6)..."
	python scripts/verify_m9_maf_pilot.py

verify-m10:
	@echo "Running M10 ASSERT / Rubric Eval verification (6/6)..."
	python scripts/verify_m10_assert.py

evo-observe:
	@echo "Current failure patterns from event history:"
	python -c "from evolution.agent import EvolutionAgent; import json; print(json.dumps(EvolutionAgent().observe(), indent=2))"

evo-propose:
	@echo "Generating and evaluating a proposal..."
	python -c "\
import asyncio, json, sys; \
sys.path.insert(0, '.'); \
from evolution.agent import EvolutionAgent; \
from evolution import evaluator; \
evo = EvolutionAgent(); \
p = evo.observe(); d = evo.diagnose(p); proposal = evo.propose(d); \
result = asyncio.run(evaluator.evaluate(proposal, fast=True)); \
print('Proposal:', json.dumps({k: proposal[k] for k in ('id','type','file','rationale')}, indent=2)); \
print('Eval:', json.dumps(result, indent=2)); \
print('Written to: evolution/proposals/pending/', proposal.get('id') + '.json')"

evo-promote:
	@echo "Pending proposals:"
	python -c "\
import json; \
from evolution.promoter import list_pending; \
pending = list_pending(); \
[print(f\"  {p['id']}  type={p['type']}  file={p['file']}  created={p['created_at']}\") for p in pending] if pending else print('  (none)'); \
print(); \
print('To promote: python -c \"from evolution.promoter import human_promote; human_promote(\\\"<id>\\\")\"')"

eval-baseline:
	@echo "Running full eval suite (20 cases, k=3) — writing baseline..."
	python evals/pass_k_runner.py --k 3 --output baseline_full.json
	python evals/ci_gate.py --update-baseline

eval-ci:
	@echo "Running CI eval gate (5 cases, k=3)..."
	python evals/ci_gate.py

eval-report:
	@echo "Running full pass^k report (20 cases, k=5)..."
	python evals/pass_k_runner.py --k 5

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
