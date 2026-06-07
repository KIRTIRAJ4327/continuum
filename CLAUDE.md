# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

**Continuum** — an enterprise agentic SDLC pipeline. It accepts a plain-English feature request and produces a merge-ready PR by running a graph of specialised AI agents: BSA → Architect → Planner → DATABASE → BACKEND → FRONTEND → Code Review → PR Review → Test → Security. Human approval gates block the graph at `story_review`, `design_review`, and `merge_review`.

## Non-negotiable Rules

Before every commit, the following must still pass:
```
python scripts/verify_agent_core.py   # must print 11/11
python scripts/verify_m0_loop.py      # must print 3/3
```

These run the **offline / deterministic path** (no Azure credentials, no Neo4j, no Postgres). Never break that path. Every new skill must have a fallback that works without any external service.

## Common Commands

```bash
# Run all tests
pytest -q

# Run a single test
pytest tests/test_agent_core.py::test_bsa_produces_story -q

# Linting (line-length = 100 enforced)
ruff check .

# Type checks
mypy orchestrator/ api/ integrations/ skills/

# Start backing services (Neo4j 5.15 on :7474/:7687, Postgres 15 on :5432)
docker compose up -d

# Smoke-test the API
uvicorn api.main:app --reload
curl -X POST http://localhost:8000/run \
     -H 'Content-Type: application/json' \
     -d '{"request": "Build a product dashboard with recent activity"}'

# Resume a paused human-gate
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
                                       ├─ _run_llm()       →  tool-call loop w/ retry/backoff
                                       └─ _run_offline()   →  deterministic fallback
```

`run_agent(state, role, ctx)` is the single entry-point. It:
1. Calls `load_agent_spec(role)` → reads `agents/<role>.yaml`
2. Calls `resolve_model(spec)` — returns `None` if no Azure env vars present
3. Delegates to `_run_llm()` (live) or `_run_offline()` (deterministic)
4. Writes results back via `apply_agent_output(state, role, data)`

### Skill loading

Skills live at `skills/<name>/v1.0/skill.py`. Because `v1.0` is not a valid Python dotted path, they are loaded with `importlib.util.spec_from_file_location()` — never with dotted imports. Each skill file exposes a single async function whose name matches the skill.

`_INJECTED_PARAMS = {"neo4j_driver", "sandbox", "repo_path", "auth_token", "jira_token", "ado_token"}` are never requested from the LLM; they are injected by `invoke_skill()` from the `AgentContext` object.

### Routing flow

```
BSA
 └─ story_review gate (interrupt) → human approves → story_approved = True
     └─ Architect
          └─ design_review gate (interrupt) → human approves → design_approved = True
               └─ Planner
                    └─ DATABASE  (migrations, seeds)
                         └─ BACKEND  (FastAPI + SQLAlchemy)
                              └─ FRONTEND  (Next.js 14 App Router)
                                   └─ local_verify gate (py_compile / ruff / mypy / pytest)
                                        └─ Security
                                             └─ merge_review gate (interrupt) → merge_approved = True
                                                  └─ [done]
```

`local_verify` fires when `current_agent in (FRONTEND, DEVELOPER)`. On failure it retries up to `gate.max_retries` times, then escalates (re-raises the interrupt with an error payload so the human can decide).

### Human gate resume

`POST /run/{run_id}/resume?approved=true` sets the corresponding approval flag on `ContinuumState`, clears `human_approval_pending`, and continues the graph with `Command(resume={"approved": bool})`. Gate-failure retries reset the gate status to "pending" instead of setting an approval flag.

### State (`orchestrator/state.py`)

`ContinuumState` is the single shared object threaded through every node. Key fields: `request`, `story`, `contract`, `schema`, `dag`, `code`, `plan`, `messages`, `gates` (list of `GateStatus`), `story_approved`, `design_approved`, `merge_approved`, `human_approval_pending`, `approval_gate_name`, `current_agent`, `next_agent`.

`AgentRole` is a `str` enum — all routing comparisons use `.value` strings or direct enum comparisons.

### Gate system (`orchestrator/gates.py`)

`gate_local_verify(ctx, state)` runs ruff → mypy → pytest in order, falling back to `py_compile` for every `.py` file when the full tools aren't installed. TypeScript files are excluded from `py_compile`. A gate failure sets `gate.status = "red"` and populates `gate.error_message`.

## Adding a New Skill

1. Create `skills/<skill_name>/v1.0/skill.py` with a single `async def <skill_name>(...)`.
2. Parameters that match `_INJECTED_PARAMS` are injected automatically; everything else is passed from the LLM tool call.
3. Provide an offline fallback: check `os.getenv("AZURE_OPENAI_API_KEY")` before any LLM call, return deterministic data if absent.
4. Register the skill in the agent's `allowed_skills` list in `agents/<role>.yaml`.
5. Ensure `scripts/verify_agent_core.py` still passes 11/11.

## Adding a New Agent

1. Create `agents/<role>.yaml` with keys: `name`, `instructions`, `model_tier` (`strong` | `cheap`), `allowed_skills`, `temperature`, `max_tokens`.
2. Add the role to `AgentRole` enum in `orchestrator/state.py`.
3. Add an offline case in `_run_offline()` in `orchestrator/agent_runner.py` (even `return {}` is fine to preserve the no-op path).
4. Wire routing in `_route()` in `orchestrator/graph.py`.
5. Add the node to `ContinuumGraph` in `graph.py`.

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

If none of the Azure vars are set, the pipeline runs fully offline — all 11 verify checks still pass.

## Key Invariants (Enforced in Code)

- **Offline path never broken** — `resolve_model()` returns `None` when no credentials; `_run_offline()` covers every role with deterministic output.
- **Skills loaded by file path** — `importlib.util.spec_from_file_location()` in `load_skill()`; dotted imports will fail because `v1.0` is not a valid identifier.
- **Injected params never LLM-requested** — `invoke_skill()` strips `_INJECTED_PARAMS` from the tool schema before binding to the LangChain tool, so the LLM never sees them.
- **Transient LLM errors retried** — `_run_llm()` retries 429/503/502/rate/timeout errors with `2^attempt` back-off (max 3 attempts).
- **Tool errors surface as ToolMessage** — exceptions in `invoke_skill()` are caught and returned as `{"error": "..."}` JSON so the LLM can self-correct rather than crashing the graph.
- **Developer sub-chain order** — DATABASE must run before BACKEND (schema first), BACKEND before FRONTEND. `local_verify` fires only after FRONTEND completes.
