#!/bin/bash
# Local verification: mirror what CI runs
set -e

echo "Running local verification..."

echo "  → Lint (ruff)..."
ruff check .

echo "  → Type check (mypy)..."
mypy . --ignore-missing-imports

echo "  → Unit tests..."
pytest tests/ -v

echo "✓ All checks passed"
