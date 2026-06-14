# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

**Continuum** — an enterprise agentic SDLC pipeline. Accepts a plain-English feature request and produces a merge-ready PR by running a graph of specialised AI agents. Human approval gates block the graph at `story_review`, `design_review`, and `merge_review`.

## Non-negotiable Rules

Before every commit, all checks must pass:
```
python scripts/verify_agent_core.py   # must print 11/11
python scripts/verify_m0_loop.py      # must print 3/3
python scripts/verify_m3_learning.py  # must print 6/6
python scripts/verify_m5_evolution.py # must print 6/6
python scripts/verify_m6_workqueue.py # must print 6/6
python scripts/verify_m7_scope_guard.py # must print 2/2
python scripts/verify_m8_repo_split.py  # must print 3/3
python scripts/verify_m9_maf_pilot.py   # must print 6/6
python evals/ci_gate.py               # must exit 0 (no regression vs baseline)
```

These run the **offline / deterministic path** — no Azure credentials, no Neo4j, no Postgres required. Never break that path. Every new skill, agent, and Evolution proposal must have a fallback that works without any external service. The verifiers degrade gracefully on a thin environment: when `ruff`/`mypy`/`pytest` are absent, `gate_local_verify` falls back to `py_compile`.

## Progress log discipline (follow this every session)

`PROGRESS.md` is the running, dated record of the project. **Every commit that
changes behaviour must add (or update) a `## YYYY-MM-DD — <title>` entry at the
top of `PROGRESS.md`** in the same session, using the format documented at the
top of that file (What / Files / Verification / Notes). Keep newest entries on
top. The "Verification" block must list the exact checks you ran and their
results — never claim a check passed without running it. When you add a new
milestone verifier, also add it to the non-negotiable list above and add a
`verify-mN` Makefile target, then keep `README.md` (badges, milestone timeline,
verification matrix) in sync.

## Common Commands

```bash
# Offline verification suite (run before every commit)
make verify-offline   # 11/11 + 3/3
make verify-m3        # 6/6 episodic memory
make verify-m4        # CI gate (regression block)
make verify-m5        # 6/6 Evolution Agent
make verify-m6        # 6/6 Work Queue + Evidence Stack
make verify-m7        # 2/2 Mapping Fidelity / Scope-Guard
make verify-m8        # 3/3 Two-Layer Repo Split
make verify-m9        # 6/6 MAF Harness Pilot

# Tests, lint, types
pytest -q
pytest tests/test_agent_core.py::test_bsa_produces_story -q
ruff check .
mypy orchestrator/ api/ integrations/ skills/

# Services + API
docker compose up -d   # Neo4j :7687, Postgres :5432
make run               # build UI + start FastAPI on :8000
make run-dev           # API only (hot-reload)
make ui-dev            # Vite dev server :5173 (proxies to :8000)

# Eval harness (M4)
make eval-ci           # 5-case fast CI check
make eval-report       # full 20-case pass^k=5 report
python evals/ci_gate.py --update-baseline   # lock in improvements

# Evolution Agent (M5) — observe/propose/promote
make evo-observe       # print current failure patterns
make evo-propose       # generate + eval a proposal, write to pending/
make evo-promote       # list pending proposals + promote command
# Apply a specific proposal (human-gated — never automatic):
python -c "from evolution.promoter import human_promote; human_promote('<proposal-id>')"

# Resume a paused human gate
curl -X POST "http://localhost:8000/run/{run_id}/resume?approved=true"
```

## Architecture

### Execution model

```
api/main.py  →  ContinuumGraph (orchestrator/graph.py)
                 │
                 └─ agent_node()  ─→  run_agent() (orchestrator/agent_runner.py)
                                       │
                                       ├─ resolve_model()  →  AzureChatCompletions | None
                                       │                       (None triggers offline path)
                                       ├─ run_maf_agent()  →  MAF pilot (opted-in roles only, M9)
                                       ├─ _run_llm()       →  tool-call loop w/ retry/backoff
                                       └─ _run_offline()   →  deterministic fallback
```

`run_agent(state, role, ctx)` is the single entry-point. It loads `agents/<role>.yaml`, resolves the model (or goes offline), then dispatches: opted-in roles (`CONTINUUM_MAF_AGENTS`) with the `agent_framework` package installed run via `run_maf_agent()` (M9 pilot); otherwise `_run_llm()`; offline → `_run_offline()`. The result is merged via `apply_agent_output(state, role, data)`. A MAF failure degrades to `_run_llm()`, then offline — never fatal.

### Routing flow

```
BSA  [graphrag_query grounds story on past episodes]
 └─ story_review gate → human approves
     └─ Architect
          └─ design_review gate → human approves
               └─ Planner  [decomposer generates parallel script if dag >= 5 tasks]
                    └─ DATABASE → BACKEND → FRONTEND
                         └─ local_verify gate (ruff / mypy / pytest / py_compile)
                              └─ Security
                                   └─ merge_review gate → human approves
                                        └─ Memory  [write_episode to Neo4j / in-memory store]
                                             └─ [done]
```

`local_verify` fires after FRONTEND (or legacy DEVELOPER). On failure it retries up to 3 times, then escalates to a human gate. `Memory` runs non-fatally after Security; failure is logged but doesn't block the run.

### State (`orchestrator/state.py`)

`ContinuumState` is the single shared object. Key fields beyond the basics:

| Field | Set by | Purpose |
|---|---|---|
| `episodes` | BSA | past episodes retrieved by graphrag_query (M3 grounding) |
| `episodes_written` | Memory | episodes written this run (M3 audit) |
| `decomposition_script` | Planner | Python fan-out script when dag has ≥ 5 tasks (M5) |
| `human_approval_pending` | _route() | blocks graph until `POST /resume` |
| `gates` | post-gate sensors | list of `GateStatus(name, status, retry_count, error_message)` |

`AgentRole` is a `str` enum — routing comparisons use `.value` or direct enum comparison.

### Skill loading

Skills live at `skills/<name>/v1.0/skill.py`, loaded via `importlib.util.spec_from_file_location()` (never dotted imports — `v1.0` is not a valid Python identifier). Each file exposes a single `async def <skill_name>(...)`.

`_INJECTED_PARAMS = {"neo4j_driver", "sandbox", "repo_path", "auth_token", "jira_token", "ado_token"}` are stripped from the LLM tool schema and injected from `AgentContext` by `invoke_skill()`.

### Gate system (`orchestrator/gates.py`)

`gate_local_verify` runs ruff → mypy → pytest in order, falling back to `py_compile` per `.py` file when tools aren't installed. TypeScript files are excluded. A gate failure sets `gate.status = "red"` and populates `gate.error_message`.

### Episodic memory (`graph_db/driver.py`, M3)

`Neo4jDriver` holds `_in_memory_episodes: List[Dict]` as an offline fallback. `write_episode()` writes to Neo4j when connected, or appends to the in-memory list otherwise. `get_similar_episodes()` does fulltext search live, or word-overlap scoring offline. The BSA offline path calls `graphrag_query` first to ground the story on past episodes.

### Eval harness (`evals/`, M4)

- `evals/golden/` — 20 requests + labels (simple/medium/complex tiers)
- `evals/scorers/deterministic.py` — 6 gate-reusing scorers, 60% weight, pass threshold = 0.8
- `evals/scorers/judge.py` — Azure OpenAI 1–5 rater, 30% weight; heuristic fallback offline
- `evals/scorers/human.py` — review queue at `evals/results/human_queue.json`, 10% weight
- `evals/ci_gate.py` — 5 cases × 3 trials; compares to `baseline.json`; exit 1 on > 2pp regression
- `evals/pass_k_runner.py` — full pass^k runner; `--k`, `--cases`, `--filter` flags

### Evolution Agent (`evolution/`, M5)

Three modules, strict separation:

- `evolution/agent.py` — `observe()` scans `event_bus._log` for failure patterns; `diagnose()` classifies them; `propose()` generates a `{type, file, before, after, rationale}` diff and writes it to `evolution/proposals/pending/`. **Never applies anything.**
- `evolution/evaluator.py` — `evaluate(proposal, fast=True)` scores the proposal with a heuristic (offline) or runs the full CI suite (live). Returns `{improved, delta_pp, safe}`.
- `evolution/promoter.py` — `human_promote(proposal_id)` is **the only code path that modifies harness files**. It applies the diff, runs `verify-offline`, and moves the proposal to `applied/` on success or `rejected/` on failure.

Proposals that would regress any eval metric are auto-rejected by the evaluator before reaching the promoter.

### Dynamic decomposer (`orchestrator/decomposer.py`, M5)

`generate_script(dag, max_agents=4, token_budget=50000)` produces a deterministic Python script that fans out to N parallel subagent slots when the Planner DAG has ≥ 5 tasks. The script is stored in `state.decomposition_script` for operator inspection; the linear DB→BE→FE chain still runs by default.

## Adding a New Skill

1. Create `skills/<skill_name>/v1.0/skill.py` with a single `async def <skill_name>(...)`.
2. Parameters in `_INJECTED_PARAMS` are injected automatically; everything else comes from the LLM tool call.
3. Provide an offline fallback — return deterministic data when `AZURE_OPENAI_API_KEY` is absent.
4. Register in `agents/<role>.yaml` → `allowed_skills`.
5. All five verify checks must still pass.

## Adding a New Agent

1. Create `agents/<role>.yaml`: `name`, `instructions`, `model_tier` (`strong`|`cheap`), `allowed_skills`, `temperature`, `max_tokens`.
2. Add the role to `AgentRole` enum in `orchestrator/state.py`.
3. Add an offline case in `_run_offline()` in `orchestrator/agent_runner.py` (bare `return {}` is valid).
4. Wire routing in `_route()` in `orchestrator/graph.py`.
5. Add the node to `ContinuumGraph._build_graph()` in `graph.py`.

## Environment Variables

| Variable | Purpose |
|---|---|
| `AZURE_OPENAI_API_KEY` | Key-based auth — triggers live LLM path |
| `AZURE_OPENAI_ENDPOINT` / `AZURE_AI_ENDPOINT` | Azure OpenAI / AI Foundry endpoint URL |
| `AZURE_OPENAI_API_VERSION` | API version (default `2024-08-01-preview`) |
| `AZURE_DEPLOYMENT_STRONG` | Deployment name for `model_tier: strong` |
| `AZURE_DEPLOYMENT_CHEAP` | Deployment name for `model_tier: cheap` |
| `AZURE_CLIENT_ID` | Managed-identity client ID (alternative to key auth) |
| `AZURE_DEVOPS_TOKEN` / `AZURE_DEVOPS_PAT` | PAT for ADO REST API v7.1 |
| `AZURE_DEVOPS_ORG_URL` | e.g. `https://dev.azure.com/myorg` |
| `AZURE_DEVOPS_PROJECT` | ADO project name |
| `FEATURE_BRANCH` | Branch name used by `approve_pr` skill |
| `NEO4J_URI` | Bolt URI (default `bolt://localhost:7687`) |
| `NEO4J_USER` / `NEO4J_PASSWORD` | Neo4j credentials |
| `POSTGRES_DSN` | DSN for `AsyncPostgresSaver` (LangGraph checkpointing) |
| `CONTINUUM_TARGET_REPO` | Target app repo path; M8 writes `.pdlc/` artifacts there |
| `CONTINUUM_MAF_AGENTS` | Comma-separated roles to run on the M9 MAF pilot (e.g. `backend`); default unset → off |

If none of the Azure vars are set, the pipeline runs fully offline — all verify checks pass.

## Key Invariants (Enforced in Code)

- **Offline path never broken** — `resolve_model()` returns `None` when no credentials; `_run_offline()` covers every role.
- **Skills loaded by file path** — `importlib.util.spec_from_file_location()`; dotted imports fail on `v1.0`.
- **Injected params never LLM-requested** — `invoke_skill()` strips `_INJECTED_PARAMS` before binding the tool schema.
- **Transient LLM errors retried** — `_run_llm()` retries 429/503/502/rate/timeout with `2^attempt` back-off (max 3).
- **Tool errors surface as ToolMessage** — `invoke_skill()` catches exceptions and returns `{"error": "..."}` so the LLM self-corrects.
- **Developer sub-chain order** — DATABASE before BACKEND (schema first), BACKEND before FRONTEND.
- **Evolution is governed** — `human_promote()` is the only write path for harness changes (Rule 9). The Evolution Agent never auto-applies a proposal. `verify-offline` runs inside `human_promote()` before the change is committed; failure reverts the file and rejects the proposal.
- **MAF pilot is dormant unless opted in (M9)** — `should_use_maf(role)` requires BOTH `CONTINUUM_MAF_AGENTS` to list the role AND the `agent_framework` package to be importable AND a live model resolved. Any MAF failure raises `MAFUnavailable` and `run_agent` falls back to `_run_llm` (then offline). The offline path never reaches MAF.
- **Windows UTF-8** — verify scripts and CI gate wrap `sys.stdout` with `io.TextIOWrapper(..., encoding="utf-8")` at the top to survive Windows cp1252 terminals. Add this to any new script that prints non-ASCII.
