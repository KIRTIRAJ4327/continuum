.PHONY: help install verify run test clean services services-down services-logs

help:
	@echo "Continuum — Agentic SDLC Pipeline"
	@echo ""
	@echo "Commands:"
	@echo "  make install        — Install Python dependencies"
	@echo "  make services       — Start Neo4j + Postgres (docker-compose, detached)"
	@echo "  make services-down  — Stop the docker-compose services"
	@echo "  make services-logs  — Tail docker-compose logs"
	@echo "  make verify         — Run local verification (lint, type, test)"
	@echo "  make run            — Start orchestrator (port 8000)"
	@echo "  make test           — Run test suite"
	@echo "  make clean          — Remove __pycache__, .pytest_cache"

install:
	pip install -r requirements.txt

services:
	docker-compose up -d neo4j postgres

services-down:
	docker-compose down

services-logs:
	docker-compose logs -f --tail=100

verify:
	@echo "Running lint..."
	ruff check .
	@echo "Running type check..."
	mypy . --ignore-missing-imports
	@echo "Running unit tests..."
	pytest tests/ -v
	@echo "✓ All checks passed"

run:
	python -m uvicorn api.main:app --reload --port 8000

test:
	pytest tests/ -v --cov=orchestrator --cov=agents

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
