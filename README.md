# Continuum — Agentic SDLC Pipeline

Plain-English feature request → merge-ready PR with passing tests + clean security scan, orchestrated by specialized AI agents in a LangGraph supervisor pattern.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set up environment
cp .env.example .env
# Edit .env with your credentials (see First-time Setup below)

# 3. Run local verification — works with zero credentials (offline path)
make verify-offline   # → 11/11 + 3/3 PASS

# 4. Start the orchestrator
make run              # builds React UI then starts FastAPI on :8000

# 5. Open the UI
open http://localhost:8000

# 6. Or submit via curl
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"request": "Build a user dashboard showing recent activity"}'
```

## First-time Setup

### Offline mode (zero credentials — always works)

The pipeline runs fully offline without any Azure account.  All agents produce
deterministic stub artifacts, and all 11/11 + 3/3 verification checks pass.

```bash
pip install -r requirements.txt
make verify-offline   # confirm offline path is healthy
make run              # server starts at http://localhost:8000
```

### Live mode (real LLM calls + Azure DevOps)

**Step 1 — Copy and populate `.env`**

```bash
cp .env.example .env
```

Edit `.env` and fill in the values marked `[REQUIRED FOR LIVE]`:

| Variable | Where to get it |
|---|---|
| `AZURE_OPENAI_API_KEY` | Azure Portal → your OpenAI resource → Keys and Endpoint |
| `AZURE_OPENAI_ENDPOINT` | Same page as above (e.g. `https://myinstance.openai.azure.com`) |
| `AZURE_DEPLOYMENT_STRONG` | Azure AI Studio → Deployments (e.g. `gpt-4o`) |
| `AZURE_DEPLOYMENT_CHEAP` | Azure AI Studio → Deployments (e.g. `gpt-4o-mini`) |
| `AZURE_DEVOPS_TOKEN` | ADO → User Settings → Personal Access Tokens (Work Items + Code scopes) |
| `AZURE_DEVOPS_ORG` | `https://dev.azure.com/your-org` |
| `AZURE_DEVOPS_PROJECT` | Project name within your org |
| `AZURE_DEVOPS_REPO` | Repository name for PR creation |

**Step 2 — Check which integrations are live**

```bash
make check-env
```

Output shows which vars are set and which integrations are in live vs stub mode.

**Step 3 — Start backing services (optional but recommended)**

```bash
docker compose up -d        # starts Neo4j on :7687 and Postgres on :5432
```

Default credentials match `.env.example` defaults — no changes needed.

**Step 4 — Run and verify**

```bash
source .env   # or: set -a; . .env; set +a
make run
```

Visit `http://localhost:8000`, submit a feature request, and watch the React
Flow graph light up node-by-node as each agent runs.

### Resume a human approval gate

```bash
# Approve
curl -X POST "http://localhost:8000/run/{run_id}/resume?approved=true"

# Reject
curl -X POST "http://localhost:8000/run/{run_id}/resume?approved=false"
```

### Development workflow

```bash
# Terminal 1 — FastAPI with hot-reload
make run-dev

# Terminal 2 — Vite dev server with API proxy
make ui-dev
# → UI at http://localhost:5173 (proxies /run, /events, /artifacts to :8000)
```

## Eval Harness (M4)

The eval harness measures **pass^k reliability** — the fraction of golden cases where ALL k consecutive trials pass. A pipeline with 70% per-trial pass rate gets only ~34% on pass^3.

### Golden dataset

`evals/golden/requests.json` — 20 feature requests across three tiers (simple CRUD → medium → complex multi-tenant/real-time). `evals/golden/labels.json` specifies what a passing output looks like per case.

### Scorer mix

| Layer | Weight | What it checks |
|---|---|---|
| Deterministic | 60% | contract_valid, sast_clean, story_has_ac, dag_has_tasks, code_has_files, schema_has_tables |
| LLM judge | 30% | Azure OpenAI rates story quality 1–5; offline heuristic fallback |
| Human review | 10% | Items where judge and deterministic disagree are queued to `evals/results/human_queue.json` |

### Commands

```bash
# First-time: run CI suite and write baseline (exits 0)
make verify-m4

# Subsequent: compare to baseline, exit 1 on any metric regressing >2pp
make eval-ci

# Full 20-case pass^k report (k=5, release gating)
make eval-report

# Re-baseline after intentional improvements
python evals/ci_gate.py --update-baseline
```

### Regression blocking

`evals/ci_gate.py` loads `evals/results/baseline.json`. If `det_weighted`, `per_trial_rate`, `pass_k_rate`, or `judge_avg` drops more than **2 percentage points** vs baseline → `exit(1)`. First run with no baseline writes it and exits 0.

All eval commands work **offline** — no Azure credentials required.

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
