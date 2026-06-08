# Continuum — Agentic SDLC Pipeline

> **Plain-English feature request → merge-ready PR**, orchestrated by a graph of specialised AI agents with human approval gates, episodic memory, a calibrated eval harness, and a self-improving Evolution Agent.

---

## Table of Contents

- [What It Does](#what-it-does)
- [How It Works](#how-it-works)
- [Agent Pipeline](#agent-pipeline)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Setup Guide](#setup-guide)
- [Developer Handoff](#developer-handoff)
- [Eval Harness (M4)](#eval-harness-m4)
- [Evolution Agent (M5)](#evolution-agent-m5)
- [API Reference](#api-reference)
- [Tech Stack](#tech-stack)
- [Milestones](#milestones)
- [Engineering Rules](#engineering-rules)

---

## What It Does

Submit a feature request like:

> *"Build a multi-tenant analytics dashboard with real-time metrics and CSV export"*

Continuum runs it through a graph of AI agents that produce:

- A structured **user story** with acceptance criteria (BSA)
- An **OpenAPI contract** + SQL schema (Architect)
- A task **DAG** with dependency ordering (Planner)
- Full **database migrations, backend API, and frontend UI** code (Developer sub-chain)
- A passing **security scan** (SAST)
- A **merge-ready PR** in Azure DevOps

Human operators approve at three checkpoints before the code lands. Every run is episodically remembered — the next run on a similar feature is faster and smarter.

---

## How It Works

### End-to-end flow

```
User submits request
        │
        ▼
  [BSA Agent] ──── GraphRAG grounds on past episodes
        │           Produces: story + acceptance criteria
        │
  [story_review gate] ── HUMAN APPROVAL ──┐
        │                                  │ reject → escalated
        ▼                                  │
  [Architect Agent]                        │
        │           Produces: OpenAPI contract + SQL schema + task DAG
        │
  [design_review gate] ── HUMAN APPROVAL ──┐
        │                                   │ reject → escalated
        ▼                                   │
  [Planner Agent]                           │
        │           Resolves DAG, orders tasks
        │           If DAG ≥ 5 tasks → decomposition script generated
        │
        ▼
  [DATABASE Agent] → migrations + seed data
        │
  [BACKEND Agent]  → FastAPI routes + SQLAlchemy models
        │
  [FRONTEND Agent] → Next.js 14 App Router pages + components
        │
  [local_verify gate] ── ruff / mypy / pytest / py_compile
        │           Retries up to 3× on failure
        │           If still failing → HUMAN APPROVAL to continue
        │
  [Security Agent] → SAST scan (bandit / semgrep)
        │
  [merge_review gate] ── HUMAN APPROVAL ──┐
        │                                  │ reject → escalated
        ▼                                  │
  [Memory Agent]                           │
        │           Writes episode to Neo4j / in-memory store
        │           Future runs retrieve this via GraphRAG
        │
        ▼
     [DONE] ── PR created in Azure DevOps
```

### Offline vs Live modes

The pipeline has two execution paths that swap transparently:

| | Offline (default) | Live |
|---|---|---|
| **LLM** | Deterministic stub artifacts | Azure OpenAI (gpt-4o / gpt-4o-mini) |
| **Neo4j** | In-memory episode store | Neo4j 5.15 via Bolt |
| **Postgres** | No checkpointing | AsyncPostgresSaver (durable resume) |
| **Azure DevOps** | No-op | Real PRs via REST API v7.1 |
| **Verify suite** | 11/11 + 3/3 + 6/6(M3) + CI-gate + 6/6(M5) all pass | Same checks, real artifacts |

Switch between modes by setting/unsetting Azure env vars. No code changes needed.

---

## Agent Pipeline

| Agent | Role | Key Skills | Output |
|---|---|---|---|
| **BSA** | Business Systems Analyst | `create_story`, `write_spec`, `graphrag_query` | Story + acceptance criteria |
| **Architect** | System designer | `emit_contract`, `emit_schema`, `write_dag` | OpenAPI contract, SQL schema, DAG |
| **Planner** | Task decomposer | `query_dag`, `make_checklist` | Ordered task list + checklist |
| **Database** | Migration author | `write_code`, `emit_schema` | SQL migrations + seeds |
| **Backend** | API implementer | `write_code`, `emit_contract` | FastAPI routes + models |
| **Frontend** | UI implementer | `write_code` | Next.js pages + components |
| **Security** | SAST scanner | `run_sast`, `scan_deps`, `secret_scan` | Issue list + gate status |
| **Memory** | Episode recorder | `write_episode`, `graphrag_query` | Episodes stored in Neo4j |
| **Evolution** | Self-improver | `read_telemetry`, `propose_patch`, `run_regression_evals` | Proposals in `pending/` |

### Human approval gates

| Gate | Fires after | Approving does |
|---|---|---|
| `story_review` | BSA | Sets `story_approved = True` → routes to Architect |
| `design_review` | Architect | Sets `design_approved = True` → routes to Planner |
| `local_verify` (escalation) | 3 failed retries | Resets retry counter → one more attempt |
| `merge_review` | Security | Sets `merge_approved = True` → runs Memory → done |

---

## Architecture

### System diagram

```
┌─────────────────────────────────────────────────────────────┐
│                        api/main.py (FastAPI)                │
│  POST /run  ·  GET /events (SSE)  ·  POST /run/{id}/resume  │
└──────────────────────────┬──────────────────────────────────┘
                           │
                    ContinuumGraph
                  orchestrator/graph.py
                           │
              ┌────────────▼────────────┐
              │    _route()  (routing)  │
              │  conditional edges on   │
              │  ContinuumState fields  │
              └────────────┬────────────┘
                           │
              ┌────────────▼────────────┐
              │  run_agent(state, role) │  ← agent_runner.py
              │                         │
              │  load_agent_spec(role)  │  ← agents/<role>.yaml
              │  resolve_model(spec)    │  ← None = offline path
              │  _run_llm() / _offline()│
              │  apply_agent_output()   │
              └────────────┬────────────┘
                           │
           ┌───────────────┼───────────────┐
           │               │               │
    ┌──────▼──────┐ ┌──────▼──────┐ ┌─────▼──────┐
    │  Neo4j      │ │  Event Bus  │ │  Skills    │
    │  driver.py  │ │  events.py  │ │  skills/   │
    │  Episodes   │ │  SSE stream │ │  v1.0/     │
    │  DAG        │ │             │ │            │
    └─────────────┘ └─────────────┘ └────────────┘
```

### Core modules

| Module | Purpose |
|---|---|
| `orchestrator/graph.py` | LangGraph `StateGraph`; routing, human gates via `interrupt()`, retry logic |
| `orchestrator/agent_runner.py` | `run_agent()` entry point; model resolution, skill binding, offline path |
| `orchestrator/state.py` | `ContinuumState` dataclass; `AgentRole` enum; all shared pipeline state |
| `orchestrator/gates.py` | Deterministic gate functions (`gate_local_verify`, `gate_sast`, `gate_contract_validate`) |
| `orchestrator/events.py` | In-memory broadcast event bus; SSE history replay for late-joining clients |
| `orchestrator/decomposer.py` | Generates parallel fan-out script for large DAGs (≥5 tasks) |
| `graph_db/driver.py` | Neo4j async driver; `write_episode`, `get_similar_episodes`; in-memory fallback |
| `evolution/agent.py` | `observe()`, `diagnose()`, `propose()` — reads telemetry, never writes harness |
| `evolution/evaluator.py` | Scores proposals via heuristic (offline) or full CI suite (live) |
| `evolution/promoter.py` | `human_promote()` — the **only** code path that modifies the harness |
| `evals/ci_gate.py` | Block-on-regression CI gate; exits 1 if any metric drops > 2pp vs baseline |
| `evals/pass_k_runner.py` | pass^k reliability runner; reports per-trial rate and pass^k |
| `api/main.py` | FastAPI server; SSE `/events`, artifact endpoints, gate resume |

### State object

`ContinuumState` is the single object threaded through every node:

```python
@dataclass
class ContinuumState:
    request: str                    # original user request
    story:   Optional[Dict]         # BSA output: title, AC, spec
    contract: Optional[str]         # OpenAPI YAML
    schema:   Optional[str]         # SQL DDL
    dag:      Optional[Dict]        # task graph + plan
    code:     Optional[Dict[str,str]] # file path → content
    gates:    List[GateStatus]      # name, status, retry_count, error
    episodes: Optional[List[Dict]]  # past episodes (M3 grounding)
    episodes_written: Optional[List[Dict]]  # this run's episodes (M3)
    decomposition_script: Optional[str]     # parallel fan-out (M5)
    story_approved: bool            # human gate flags
    design_approved: bool
    merge_approved: bool
    human_approval_pending: bool
```

### Skill system

Skills live at `skills/<name>/v1.0/skill.py`. Each exposes one `async def <name>(...)`.

- Loaded via `importlib.util.spec_from_file_location()` — `v1.0` is not a valid Python identifier so dotted imports would fail.
- Parameters in `_INJECTED_PARAMS` (`neo4j_driver`, `sandbox`, `repo_path`, etc.) are injected from `AgentContext`; the LLM never sees them.
- Every skill must have an offline fallback — return deterministic data when Azure creds are absent.

---

## Quick Start

```bash
# Clone and install
git clone https://github.com/KIRTIRAJ4327/continuum.git
cd continuum
pip install -r requirements.txt

# Verify everything works offline (no credentials needed)
make verify-offline     # → 11/11 + 3/3 PASS

# Start the server
make run                # builds React UI → FastAPI on :8000

# Submit a request
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"request": "Build a product dashboard with recent activity feed"}'

# Open the live UI
open http://localhost:8000
```

---

## Setup Guide

### Offline mode (zero credentials)

```bash
pip install -r requirements.txt
make verify-offline
make run
```

All agents run deterministically. The UI shows the full pipeline flow. No Azure account needed.

### Live mode (real LLM + Azure DevOps)

**1. Copy and populate `.env`**

```bash
cp .env.example .env
```

| Variable | Where to get it |
|---|---|
| `AZURE_OPENAI_API_KEY` | Azure Portal → OpenAI resource → Keys and Endpoint |
| `AZURE_OPENAI_ENDPOINT` | Same page (e.g. `https://myinstance.openai.azure.com`) |
| `AZURE_DEPLOYMENT_STRONG` | Azure AI Studio → Deployments (e.g. `gpt-4o`) |
| `AZURE_DEPLOYMENT_CHEAP` | Azure AI Studio → Deployments (e.g. `gpt-4o-mini`) |
| `AZURE_DEVOPS_TOKEN` | ADO → User Settings → Personal Access Tokens |
| `AZURE_DEVOPS_ORG_URL` | `https://dev.azure.com/your-org` |
| `AZURE_DEVOPS_PROJECT` | Project name within your org |
| `NEO4J_URI` | `bolt://localhost:7687` (docker-compose default) |
| `NEO4J_PASSWORD` | `continuum-dev` (docker-compose default) |

**2. Check integration status**

```bash
make check-env
# Shows: Azure AI: LIVE / offline, ADO: LIVE / offline, Neo4j: configured / default
```

**3. Start backing services**

```bash
docker compose up -d    # Neo4j 5.15 on :7687, Postgres 15 on :5432
```

**4. Run**

```bash
make run    # visit http://localhost:8000
```

**Development workflow (hot-reload)**

```bash
# Terminal 1
make run-dev    # FastAPI with --reload on :8000

# Terminal 2
make ui-dev     # Vite dev server on :5173 (proxies /run, /events, /artifacts to :8000)
```

### Human gate approval

```bash
# Get the run ID from the /run response or UI
curl -X POST "http://localhost:8000/run/{run_id}/resume?approved=true"   # approve
curl -X POST "http://localhost:8000/run/{run_id}/resume?approved=false"  # reject
```

---

## Developer Handoff

### Adding a skill

1. Create `skills/<skill_name>/v1.0/skill.py` with a single `async def <skill_name>(...)`.
2. Parameters matching `_INJECTED_PARAMS` are auto-injected from context; everything else comes from the LLM tool call.
3. Return deterministic data when `AZURE_OPENAI_API_KEY` is unset (offline invariant).
4. Add the skill name to `allowed_skills` in `agents/<role>.yaml`.
5. Run all verify checks — they must all still pass.

```python
# skills/my_skill/v1.0/skill.py
async def my_skill(param: str, neo4j_driver=None) -> dict:
    if neo4j_driver is None:
        return {"result": "offline stub"}
    # live path
    ...
```

### Adding an agent

1. Create `agents/<role>.yaml`:

```yaml
name: "My Agent"
model_tier: "cheap"          # or "strong"
temperature: 0.3
max_tokens: 1500
allowed_skills:
  - my_skill
instructions: |
  You are the My Agent. Your task is ...
  Output JSON: { "key": "value" }
```

2. Add to `AgentRole` enum in `orchestrator/state.py`.
3. Add an offline case in `_run_offline()` in `orchestrator/agent_runner.py` (bare `return {}` keeps the offline path intact).
4. Wire routing in `_route()` in `orchestrator/graph.py`.
5. Add the node to `ContinuumGraph._build_graph()`.

### Running the verify suite

```bash
make verify-offline       # 11/11 + 3/3  — core offline invariant
make verify-m3            # 6/6           — episodic memory learning
make verify-m4            # CI gate       — eval regression check
make verify-m5            # 6/6           — Evolution Agent flow
```

All four must pass before every commit. They require zero credentials.

### Branch strategy

```
main        ← stable releases (tagged m0-baseline … m5-baseline)
dev         ← integration branch (PR target)
feature/m*  ← milestone feature branches (PR → dev)
```

---

## Eval Harness (M4)

Measures **pass^k reliability**: the fraction of golden cases where ALL k consecutive trials pass.

> A pipeline with 70% per-trial reliability gets only ~34% on pass^3. Both rates are reported so the gap is visible.

### Dataset

`evals/golden/requests.json` — 20 feature requests across three tiers:

| Tier | Count | Characteristics |
|---|---|---|
| Simple (CRUD) | 5 | Single resource, REST endpoints |
| Medium | 7 | Multiple resources, auth, pagination |
| Complex | 8 | Multi-tenant, real-time, integrations |

### Scorer mix

| Scorer | Weight | What it checks |
|---|---|---|
| Deterministic | 60% | `contract_valid`, `sast_clean`, `story_has_ac`, `dag_has_tasks`, `code_has_files`, `schema_has_tables` |
| LLM judge | 30% | Azure OpenAI rates story quality 1–5; offline heuristic fallback |
| Human review | 10% | Cases where judge and deterministic disagree (Δ ≥ 0.25) are queued to `evals/results/human_queue.json` |

### Regression gate

`evals/ci_gate.py` compares `det_weighted`, `per_trial_rate`, `pass_k_rate`, and `judge_avg` to `evals/results/baseline.json`. Any metric dropping more than **2 percentage points** → `exit(1)`.

```bash
make eval-ci          # fast check (5 cases × 3 trials)
make eval-report      # full report (20 cases × 5 trials)
make eval-baseline    # run full suite and write new baseline
python evals/ci_gate.py --update-baseline   # lock in improvements
```

All eval commands work **offline**.

---

## Evolution Agent (M5)

Closes the self-improvement loop: observes telemetry, diagnoses failures, proposes fixes, evaluates them, and surfaces them for human promotion.

**Rule 9 — governed mutation: no proposal is ever auto-applied.**

### Pipeline

```
event_bus._log
      │
  observe()          scan failure rates, gate failures, retries, escalations
      │
  diagnose()         classify: prompt_issue / gate_too_strict / skill_gap / routing_error
      │
  propose()          generate {type, file, before, after, rationale}
      │              write to evolution/proposals/pending/
      │
  evaluate()         heuristic (offline) or full CI suite (live)
      │              auto-reject if any metric regresses > 2pp
      │
  human_promote()    ONLY write path to the harness
      │              apply diff → verify-offline → applied/ or rejected/
      ▼
  evolution/proposals/
    pending/         awaiting operator review
    applied/         promoted + verified
    rejected/        auto-rejected or verify-failed
```

### Commands

```bash
make evo-observe      # print current failure patterns
make evo-propose      # generate + evaluate a proposal
make evo-promote      # list pending proposals

# Apply a specific proposal (runs verify-offline automatically):
python -c "from evolution.promoter import human_promote; human_promote('evo-abc12345')"
```

### Dynamic decomposition

When the Planner produces a DAG with ≥ 5 tasks, `orchestrator/decomposer.py` generates a Python orchestration script that fans out to **max 4 parallel subagent slots** with a **50k total token budget**. The script is stored in `state.decomposition_script` for operator inspection; the default linear chain still runs.

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/run` | Submit a feature request; returns `{run_id, status}` |
| `GET` | `/runs` | List all run IDs and statuses |
| `GET` | `/run/{run_id}` | Poll run status and artifact summary |
| `GET` | `/events/{run_id}` | SSE stream of agent events (real-time) |
| `POST` | `/run/{run_id}/resume` | Resume a human gate (`?approved=true/false`) |
| `GET` | `/artifacts/{run_id}/{agent}` | Fetch agent artifact by role name (see below) |
| `GET` | `/health` | Health check |

### Submit a request

```bash
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"request": "Build a user notification system with email + in-app alerts"}'
# → {"run_id": "abc-123", "status": "running"}
```

### Stream events

```bash
curl -N http://localhost:8000/events/abc-123
# → data: {"event_type": "agent_start", "agent": "bsa", ...}
# → data: {"event_type": "agent_complete", "agent": "bsa", "data": {"duration_s": 1.2}}
# → data: {"event_type": "gate_green", "gate": "local_verify", ...}
# → data: {"event_type": "gate_red", "gate": "local_verify", "data": {"output": "..."}}
# → data: {"event_type": "human_gate_pending", "gate": "merge_review", ...}
# → data: {"event_type": "run_complete", "run_id": "abc-123", ...}
```

### Fetch an artifact

The `{agent}` path parameter is the agent role name:

| Agent | Returns |
|---|---|
| `bsa` | `{story: {title, acceptance_criteria, ...}}` |
| `architect` | `{contract: "...", schema: "...", dag: {...}}` |
| `planner` | `{plan: {tasks: [...], checklist: [...]}}` |
| `developer` / `backend` / `frontend` | `{code: {"path/file.py": "content", ...}}` |
| `security` | `{gate_status: "green/red", error: null}` |
| `memory` | `{episodes_retrieved: [...], episodes_written: [...]}` |

```bash
curl http://localhost:8000/artifacts/abc-123/bsa
# → {"run_id": "abc-123", "agent": "bsa", "story": {"title": "...", ...}}

curl http://localhost:8000/artifacts/abc-123/architect
# → {"run_id": "abc-123", "agent": "architect", "contract": "openapi: ...", "schema": "CREATE TABLE ..."}
```

### Approve a gate

```bash
curl -X POST "http://localhost:8000/run/abc-123/resume?approved=true"
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Orchestration** | LangGraph (supervisor `StateGraph`, `interrupt()` for durable human gates) |
| **LLM runtime** | Azure AI Foundry (tiered: `gpt-4o` strong, `gpt-4o-mini` cheap) |
| **Graph / Memory** | Neo4j 5.15 (task DAG, episodic memory, GraphRAG fulltext search) |
| **Checkpointing** | Azure Database for PostgreSQL (`AsyncPostgresSaver`, durable run resume) |
| **Integrations** | Azure DevOps REST API v7.1 (work items, PRs, wiki) |
| **Security scanning** | bandit + semgrep (SAST gate) |
| **Backend API** | FastAPI + asyncio, SSE for real-time streaming |
| **Frontend** | React 18 + React Flow (graph visualisation), Vite, Tailwind CSS |
| **Agent code output** | Next.js 14 App Router + FastAPI + SQLAlchemy (generated by pipeline) |

---

## Milestones

| Milestone | What shipped |
|---|---|
| **M0** | Walking skeleton — BSA → Architect → Developer offline, verify suite passes |
| **M1** | Human gates (`interrupt()`), max-3 retry loop, Azure DevOps PR creation, developer sub-chain (DB → BE → FE) |
| **M2** | Real-time React Flow UI, SSE event streaming, gate inbox, artifact viewer |
| **M3** | Neo4j episodic memory (`write_episode`), GraphRAG grounding for BSA, in-memory offline store |
| **M4** | pass^k eval harness, 20-case golden dataset, calibrated judge, block-on-regression CI gate |
| **M5** | Evolution Agent (observe → propose → human promote), dynamic decomposition for large DAGs |

---

## Engineering Rules

These are enforced in code, not just policy:

1. **Never push and pray** — `make verify-offline` passes before every commit
2. **Every stage is a gate** — conditional edges only; no skip paths in `graph.py`
3. **Verify locally what CI verifies remotely** — same ruff/mypy/pytest toolchain
4. **Bound the auto-fix loop** — max 3 retries per gate, then human escalation
5. **Skills are atoms, not prompts** — versioned, named, independently testable
6. **Production deploys are retags** — promote artifacts, never rebuild from scratch
7. **The pipeline is the product** — harness quality > model quality; models are swappable
8. **Plans are contracts** — PEV loop enforces machine-checked specs before dev starts
9. **The harness is governed** — Evolution Agent proposals require `human_promote()` before any harness file changes

---

## Documentation

- `Continuum-Agentic-SDLC-PRD.md` — full PRD + architecture narrative
- `Continuum-Architecture.mermaid` — system diagram
- `CLAUDE.md` — guidance for Claude Code (AI assistant instructions)
