# Sample Target App

Minimal **FastAPI + React** application demonstrating the **Continuum M8 Two-Layer
Repo Split (D33)**.

## What this is

This is a Layer 2 target application — the repo that Continuum builds *into*.
The Continuum pipeline (Layer 1) generates code, contracts, and evidence here,
writing artifacts into `.pdlc/`.

## Structure

```
samples/target-app/
├── src/
│   ├── __init__.py
│   └── main.py            ← minimal FastAPI entry point
├── tests/
│   └── __init__.py
├── requirements.txt
└── .pdlc/                 ← Continuum writes here (run artifacts)
    └── README.md
```

## Running

```bash
pip install -r requirements.txt
uvicorn src.main:app --reload
# → http://localhost:8001
```

## Pointing Continuum at this repo

Set `CONTINUUM_TARGET_REPO` to the absolute path of this directory, then run
a pipeline:

```bash
export CONTINUUM_TARGET_REPO=/path/to/samples/target-app
make run-dev   # starts Continuum API on :8000
curl -X POST http://localhost:8000/run \
  -H 'Content-Type: application/json' \
  -d '{"request": "Add a products listing page"}'
```

After the run completes, inspect `.pdlc/` for the generated artifacts.
