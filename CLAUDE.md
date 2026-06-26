# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

**Continuum** — an enterprise agentic SDLC pipeline. Accepts a plain-English feature request and produces a merge-ready PR by running a graph of specialised AI agents. Human approval gates block the graph at `story_review`, `design_review`, and `merge_review`.

The active product spec is **`Continuum-PRD-v3.0.md`** (M0–M10 delivered baseline + M11–M14 roadmap). M0–M10 are shipped and offline-verified; the forward roadmap and the production-readiness track (P0–P2) are documented in the "Roadmap" and "Production Readiness" sections below.

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
python scripts/verify_m10_assert.py     # must print 6/6
python scripts/verify_m11_spec_registry.py # must print 4/4
python scripts/verify_m12_compliance.py     # must print 3/3
python scripts/verify_m13_state_machine.py  # must print 6/6
python scripts/verify_p0_durable_execution.py  # must print 4/4
python scripts/verify_p02_boxlite.py           # must print 4/4
python scripts/verify_p1_gate_independence.py # must print 6/6
python scripts/verify_c1_correctness.py        # must print 5/5
python scripts/verify_c2_observability.py      # must print 3/3
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
make verify-m10       # 6/6 ASSERT / Rubric Eval Integration
make verify-m11       # 4/4 Spec Registry
make verify-m12       # 3/3 Compliance Report
make verify-m13       # 6/6 15-State SDLC Machine
make verify-p1        # 6/6 Gate Independence (lint/typecheck/test)

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

**P1.1 (gate independence):** local verification is three INDEPENDENT gates —
`gate_lint` (ruff), `gate_typecheck` (mypy), `gate_test` (pytest) — each degrading
to `py_compile` on a thin environment. `gate_local_verify_split()` runs each once
and returns `{"lint","typecheck","test"}`; `gate_local_verify()` aggregates them
(green iff all pass) as the backward-compatible composite the live retry loop keys
on. The DEVELOPER path records all three as their own `GateStatus` **plus** the
composite `local_verify`, so the Evidence Stack reads layer 1 (build = lint+typecheck)
and layer 2 (regression = test) from *different* signals (resolves OQ-3); when only
`local_verify` exists (pre-P1.1 states) both layers fall back to it. A gate failure
sets `gate.status = "red"` and populates `gate.error_message`. TypeScript files are
excluded. (Per-gate retry *budgets* in the live loop remain a follow-up tied to P0.1.)

### Episodic memory (`graph_db/driver.py`, M3)

`Neo4jDriver` holds `_in_memory_episodes: List[Dict]` as an offline fallback. `write_episode()` writes to Neo4j when connected, or appends to the in-memory list otherwise. `get_similar_episodes()` does fulltext search live, or word-overlap scoring offline. The BSA offline path calls `graphrag_query` first to ground the story on past episodes.

### Eval harness (`evals/`, M4)

- `evals/golden/` — 20 requests + labels (simple/medium/complex tiers)
- `evals/scorers/deterministic.py` — 6 gate-reusing scorers, 60% weight, pass threshold = 0.8
- `evals/scorers/judge.py` — **ASSERT-driven rubric (M10)**, 30% weight; `score()` returns the weighted pass rate over the applicable ASSERT trial specs. Optional Azure 1–5 rating is kept only as a secondary `llm_rating` calibration signal — it no longer drives the score. Deterministic + offline.
- `evals/scorers/human.py` — review queue at `evals/results/human_queue.json`, 10% weight
- `evals/ci_gate.py` — 5 cases × 3 trials; compares to `baseline.json`; exit 1 on > 2pp regression
- `evals/pass_k_runner.py` — full pass^k runner; `--k`, `--cases`, `--filter` flags

### ASSERT specs (`evals/assert_specs.py`, M10)

Declarative, machine-checkable rubrics — one or more per governance Rule (1–9)
plus the M7 scope invariant — that replace the old opaque 1–5 judge. Each
`AssertSpec` has a `check(state, label) -> (verdict, detail)` where verdict ∈
`{"pass","fail","na"}`. Two families:

- `TRIAL_SPECS` — graded per run against `ContinuumState` (Rules 1, 2, 4, 8 + M7
  scope). `evaluate_specs(state)` returns `{value, specs, n_pass, n_fail, n_na,
  n_applicable}`; `value` is the weighted pass rate over applicable specs (`na`
  excluded, weight redistributed). This is what `judge.py` now reports.
- `GOVERNANCE_SPECS` — structural harness invariants (Rules 3, 5, 6, 7, 9), graded
  by `evaluate_governance()`. The verify script asserts these; they don't depend
  on any run.

Pure and offline-safe (no LLM, no network). `evidence_stack.build_evidence_stack(
state, asserts=...)` accepts the `specs` mapping from `evaluate_all(state)` and
annotates each layer with the rubric verdict backing it (`assert_rule`,
`assert_verdict`); omitting `asserts` yields the exact pre-M10 6-layer shape.

### Evolution Agent (`evolution/`, M5)

Three modules, strict separation:

- `evolution/agent.py` — `observe()` scans `event_bus._log` for failure patterns; `diagnose()` classifies them; `propose()` generates a `{type, file, before, after, rationale}` diff and writes it to `evolution/proposals/pending/`. **Never applies anything.**
- `evolution/evaluator.py` — `evaluate(proposal, fast=True)` scores the proposal with a heuristic (offline) or runs the full CI suite (live). Returns `{improved, delta_pp, safe}`.
- `evolution/promoter.py` — `human_promote(proposal_id)` is **the only code path that modifies harness files**. It applies the diff, runs `verify-offline`, and moves the proposal to `applied/` on success or `rejected/` on failure.

Proposals that would regress any eval metric are auto-rejected by the evaluator before reaching the promoter.

### Dynamic decomposer (`orchestrator/decomposer.py`, M5)

`generate_script(dag, max_agents=4, token_budget=50000)` produces a deterministic Python script that fans out to N parallel subagent slots when the Planner DAG has ≥ 5 tasks. The script is stored in `state.decomposition_script` for operator inspection; the linear DB→BE→FE chain still runs by default.

### Spec Registry (`graph_db/spec_registry.py`, M11)

Persistent, versioned, append-only store of the agreed spec per **component** (a
deterministic slug from `orchestrator/component.py` `component_slug(request, story)`).
A `Spec` is one immutable version; a new version that materially differs SUPERSEDES
the prior one (no in-place edits). Three-tier fallback mirrors the M3 driver:
live Neo4j session → process-level `_IN_MEMORY_SPECS` list → pure-Python lookups.
The store is **module-level** so run N's spec is visible to run N+1 and to the
read-only API within one process — that's what gives cross-run persistence offline,
with no driver singleton plumbing. The live path runs only when a *connected*
`Neo4jDriver` is passed (`_is_live()`); any error falls back to the store.

BSA flow (`_run_offline`, role==bsa): `query_spec_registry` retrieves the prior
current spec **before** drafting, then `write_spec_registry` persists this run's
spec as a new version, recording a supersession via `specs_match()` when the body
materially differs. Results land on `state.component`, `state.registry_specs`,
`state.registry_current_before`, `state.spec_superseded`. `gate_scope_conformance`
(M7) gained a Registry-conformance branch: when a prior spec exists, the run's spec
must `specs_match()` it **or** carry a `spec_superseded` record — undeclared drift
is red. `GET /specs` lists components; `GET /specs/{component}` returns the version
chain. `specs_match()` (stable keys: `api_endpoints`, `data_entities`,
`out_of_scope`) is the single source of truth for conformance, shared by the write
skill and the gate.

### Compliance Report (`api/compliance.py`, M12)

`build_compliance_report(run_id, state, events=None)` assembles an auditor-readable
artifact for a run — **packaging, not new capability**. Eight sections, always
present and in `SECTION_ORDER`: `run_metadata`, `spec` (prefers the M11 Registry,
falls back to work-item state), `business_mappings` (+ scope-guard conformance),
`gate_decisions` (human G1/G2 + automated gates), `evidence_stack` (reuses
`build_evidence_stack`), `audit_trail` (event-bus history, injectable), `versions`
(`git describe` + model mode/tiers), and `compliance_assertions` (a boolean checklist
+ `passed`). When data is absent it is an explicit `null`/`[]` **with a
`_missing_reason`**, enumerated in the top-level `missing` list — never silently
omitted (`complete == (missing == [])`). Pure/offline: the audit trail reads the
in-memory event bus when not injected; `config`/`git` are best-effort lazy lookups.
`GET /runs/{run_id}/compliance-report` (JSON) and `…/compliance-report.html`
(`render_compliance_html`). Approver identity/timestamps are explicit nulls until
auth lands (P0.3).

### 15-State SDLC Machine (`orchestrator/state_machine.py` + `policy_engine.py`, M13)

The lifecycle is an explicit, policy-governed graph. `state_machine.py` *declares*
it (pure data, zero orchestrator imports so `state.py` can import `SDLCState`
cycle-free): the 15 `SDLCState`s (`NEW → … → CLOSED`), a `StateDefinition` per state
(entry `required_artifacts` + `quality_gates`, allowed `forward` + `returns` edges,
SLA), and `TRANSITION_GATES` mapping the four named human gates to the exact edges
they govern — **G1** NEW→EPIC_APPROVED, **G2** ARCH_READY→IMPL_READY, **G3**
RELEASE_READY→DEPLOYED, **G4** IN_PRODUCTION→IN_PROGRESS (incident return). Return
edges (tests-fail, security findings, prod incident) are first-class.

`policy_engine.py` *enforces* it: `can_transition(artifact, from_state, to_state)
-> (ok, reason)` is a pure predicate checking, in order, edge-exists → human gate
granted → required artifacts present → quality gates green. `advance(artifact,
to_state)` mutates `artifact.lifecycle_state` only when the policy permits. Both read
the artifact (a `ContinuumState`) via `getattr`, so they never import `state.py`.

`ContinuumState.lifecycle_state` (default `SDLCState.NEW`) is the artifact's record;
`incident_approved` grants G4. The live `_execute_pipeline()` path is **unchanged** —
the API surfaces a read-only `lifecycle_state` via `derive_lifecycle_state(artifact)`
(best-effort, never raises), and the existing `story/design/merge` approvals map to
G1/G2/G3 (`GATE_APPROVAL_FIELD`). Wiring the policy engine as the live *gating*
mechanism (replacing `_route()`) is a follow-up tied to P0.1.

## Roadmap (M14) — not yet implemented

The active spec is **`Continuum-PRD-v3.0.md`** (§8). M11, M12 and M13 are shipped (see
the architecture sections above). M14 is **planned** — no code exists yet, so do
**not** add its verify script to the non-negotiable list until it exists. Guard-rails
(one milestone per feature branch → PR to `dev`, per PRD §12):

- **M14 MAF Graduation** (Frontier, gated on M13) — graduates the M9 pilot; **not
  offline-verifiable** (needs `agent_framework` + Hyperlight). Keep the
  dormant-unless-opted-in convention; never claim a passing offline badge.

Convention reminders (PRD §12): events are plain dicts
`{event_type, agent, run_id, data, timestamp}`; `started_at`/`completed_at` are
`Optional[float]`; UI uses Unicode glyphs (no icon lib); `api/main.py`'s
`_execute_pipeline()` + `_RUNS` is the live path (`graph.py`'s `ContinuumGraph` is
not); **every** new field/event/skill must be offline-safe.

## Production Readiness (P0–P2) — operational track

The architecture is production-grade; these five gaps are *hardening*, not redesign.
Sequenced P0→P2, and **P0 comes before the M11–M14 feature milestones**. Largely
operational and partly not offline-verifiable — but the offline-safe invariant for
the verify suite still holds throughout.

- **P0.1 Durable execution** — ✅ **shipped** (see the Key Invariants section below):
  `graph_db/run_store.py` `RunStore` — Postgres-backed when `POSTGRES_DSN` is set,
  in-memory dict otherwise. `_RUNS` in `api/main.py` is now an alias of the store's
  `_MEMORY` dict (same object, no behaviour change for the offline verify suite). On
  startup, active runs are restored from Postgres (`restore_active()`). Checkpoints are
  written at every human-gate suspension and at pipeline completion. The
  `ContinuumGraph._run_agent()` per-run-context bug is also fixed. `verify_p0_durable_execution.py` 4/4.
- **P0.2 Sandbox hardening** — `sandbox/` must provision a real ACA/Hyper-V session
  with lifecycle + timeout + egress + credential isolation; **no local-exec fallback
  in production** (offline stub stays for the verify suite only).
- **P0.3 Auth + tenancy** ("M-Auth", ideally before M11) — identity on every run and
  gate approval, RBAC (dev/reviewer/admin), per-tenant Neo4j subgraph / audit trail /
  spec registry. This is the gate for any real-client use.
- **P1.1 Gate independence** — ✅ **shipped** (see the Gate system section above):
  `gate_lint` / `gate_typecheck` / `gate_test` are separate gates with separate
  evidence records; the Evidence Stack's layers 1 & 2 now read different signals
  (resolves OQ-3) and M12's compliance claims are defensible. Per-gate retry
  *budgets* in the live loop remain a follow-up tied to P0.1.
- **P1.2 Observability** — OTel (M10 hook) exported to App Insights / Langfuse: real
  traces, real cost (not the offline `$0.02` stub), per-stage latency, stuck-run alerts.

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
| `CONTINUUM_OTEL` | When truthy AND `opentelemetry-api` is importable, the event bus mirrors events onto OTel spans (M10); default unset → off (no-op) |

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
- **ASSERT rubric is deterministic + offline (M10)** — `evals/assert_specs.py` specs are pure predicates over state/harness; `judge.score()` never needs credentials. The optional Azure 1–5 rating is a secondary `llm_rating` calibration signal, never the score. A buggy spec is caught and converted to a deterministic `fail` (`_safe_check`), so it can't crash the eval.
- **OTel export is dormant unless opted in (M10)** — `event_bus.emit()` mirrors events onto OTel spans only when `CONTINUUM_OTEL` is truthy AND `opentelemetry` imports; otherwise `_otel_emit()` is a no-op. Any OTel error is swallowed — telemetry can never break a run, and the offline path never imports OTel.
- **Spec Registry is append-only + offline-safe (M11)** — `graph_db/spec_registry.py` never edits a spec in place; a materially different spec creates a new version that SUPERSEDES the prior. The module-level `_IN_MEMORY_SPECS` store gives cross-run persistence with no Neo4j; the live Neo4j path runs only when a *connected* driver is passed (`_is_live()`) and any error falls back to the store. The M11 scope-guard branch only fires when `state.registry_current_before` is set, so M0–M10 runs are byte-unchanged. `specs_match()` is the single conformance predicate shared by the write skill and the gate.
- **Compliance Report is total + honest (M12)** — `build_compliance_report()` always returns all 8 sections; absent data is an explicit `null`/`[]` with a `_missing_reason` and is listed in `missing` (never silently dropped). It is pure/offline (event-bus audit trail, lazy `config`/`git`), reuses `build_evidence_stack`, and never asserts more than the run actually proved — a blocked/returned run yields a valid report whose `compliance_assertions.passed` is honestly `False`.
- **State machine governs, offline-safe (M13)** — `policy_engine.can_transition()` is a pure predicate over the artifact and the declared `state_machine.py` graph (edge → gate → artifacts → quality gates); `state_machine.py` imports nothing from the orchestrator so `state.py` can import `SDLCState` cycle-free. The live `_execute_pipeline()` path is unchanged — `lifecycle_state` is surfaced read-only via `derive_lifecycle_state()` (best-effort, never raises). M0–M12 runs are byte-unchanged.
- **Gates are independent (P1.1)** — `gate_lint`/`gate_typecheck`/`gate_test` are separate gates with separate `GateStatus` records; `gate_local_verify` is the backward-compatible aggregate the live retry loop and the M0 verifier key on (M0 patches `gate_local_verify_split`, the single-run source of truth). The Evidence Stack reads layers 1 & 2 from different signals when the split gates exist, else falls back to `local_verify` — so pre-P1.1 states are byte-unchanged. Each gate keeps its `py_compile` offline fallback.
- **Run store is durable + offline-safe (P0.1)** — `graph_db/run_store.py` `RunStore`
  writes a checkpoint to Postgres (asyncpg) at every human-gate suspension and at
  pipeline completion. `_RUNS` in `api/main.py` is the module-level `_MEMORY` dict from
  `run_store` — same object, no copy, no behaviour change for the offline verify suite.
  On startup (`restore_active()`) non-terminal runs are reloaded from Postgres so they
  survive a process restart. Any Postgres failure degrades silently to the in-memory tier
  — the offline-safe invariant holds through transient outages, not just zero-credential
  environments. `ContinuumGraph._run_agent()` creates a per-run `AgentContext`
  (concurrent-run-safe, correct `run_id` for event emission). `verify_p0_durable_execution.py` 4/4.
- **Windows UTF-8** — verify scripts and CI gate wrap `sys.stdout` with `io.TextIOWrapper(..., encoding="utf-8")` at the top to survive Windows cp1252 terminals. Add this to any new script that prints non-ASCII.
