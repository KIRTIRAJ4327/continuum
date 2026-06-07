# Continuum — Agentic SDLC Pipeline

Plain-English feature request → merge-ready PR with passing tests + clean security scan, orchestrated by specialized AI agents in a LangGraph supervisor pattern.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set up environment
cp .env.example .env
# Edit .env with your credentials

# 3. Run local verification (lint/type/test)
make verify

# 4. Start the orchestrator (M0 demo)
make run

# 5. Submit a feature request
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"request": "Build a user dashboard showing recent activity"}'
```

## Project Structure

See `Cowork-Project-Blueprint.md` for the full folder layout and M0–M5 breakdown.

## Tech Stack

- **Orchestration:** LangGraph (supervisor pattern)
- **Runtime:** Azure AI Foundry (tiered models)
- **Graph/Memory:** Neo4j (DAG, GraphRAG, episodic memory)
- **Sandbox:** Azure Container Apps dynamic sessions (Hyper-V isolated)
- **State:** Azure DB for PostgreSQL (durable checkpoints)
- **Integrations:** Azure DevOps Remote MCP (work items, PRs, wiki)
- **Frontend:** React + React Flow (M2)
- **Backend:** FastAPI (M2+)

## Milestones

- **M0:** Walking skeleton (story → contract → code, in sandbox)
- **M1:** Gates + loops + Azure DevOps (real PRs, max-3 retries)
- **M2:** Live UI (React Flow, activity stream, gate inbox)
- **M3:** Memory & learning (Neo4j episodic memory, GraphRAG)
- **M4:** Eval harness (pass^k, calibrated judges, block-on-regression)
- **M5:** Scale & self-improve (dynamic decomposition, Evolution Agent)

## Key Rules (enforced in code)

1. **Never push and pray** → local verify before any push
2. **Every stage is a gate** → conditional edges, no skip paths
3. **Verify locally what CI verifies remotely** → same toolchain, drift check
4. **Bound the auto-fix loop** → max 3 retries per gate
5. **Skills are atoms, not prompts** → versioned, named tools
6. **Production deploys are retags** → promote artifacts, never rebuild
7. **The pipeline is the product** → harness is optimized, models are swappable
8. **Plans are contracts** → explicit machine-checked specs
9. **The harness is governed** → eval-gated changes only

## Documentation

- `Continuum-Agentic-SDLC-PRD.md` — full PRD + architecture narrative
- `Continuum-Architecture.mermaid` — system diagram
- `Continuum-Research-Report.md` — research backing v2.2

## Contributing

See individual `README.md` files in each module folder for design docs.
