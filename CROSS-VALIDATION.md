# Continuum Cross-Validation Report
**Sources:** PRD v2.2 · Architecture (mermaid) · Cowork Blueprint · Claude Code Handoff
**Date:** 2026-06-06

---

## Summary

| Area | Status | Critical Issues |
|---|---|---|
| Folder structure | ⚠️ Near-match | 3 location mismatches, 2 missing files |
| Orchestrator state | ✅ Aligned | — |
| LangGraph graph shape | ⚠️ Partial | Single dev node vs 3 parallel dev agents (M1 concern) |
| Agent roster | ⚠️ M0-scoped | M0 agents present; M1–M5 agents expected missing |
| Skills coverage | ❌ Gaps | 4 M0-critical skills missing stubs |
| Architecture planes | ✅ Aligned | All planes mapped to modules |
| Neo4j schema | ✅ Aligned | Constraints, indexes, queries all match PRD §7 |
| PRD rules 1–9 | ⚠️ Partial | PEV loop and 3-tier perms stubbed but misplaced |

---

## 1. Folder Structure

### ✅ Matches PRD §14

All major directories present and accounted for.

### ⚠️ Location Mismatches

| File | PRD §14 Location | Scaffolded Location | Fix |
|---|---|---|---|
| `pev.py` | `harness/pev.py` | `orchestrator/pev.py` | Move to `harness/` — PRD defines it as the harness governor, not an orchestration primitive |
| `permissions.py` | `harness/permissions.py` | `orchestrator/permissions.py` | Move to `harness/` — it's a harness-enforced tier, not orchestrator state |
| `aca_sessions.py` | `sandbox/aca_sessions.py` | `sandbox/aca_client.py` | Minor: Blueprint/Handoff use `aca_client.py` — keep this name, update PRD reference or add alias |

### ❌ Missing Files

| File | Milestone | Priority |
|---|---|---|
| `harness/ci-mirror.Dockerfile` | M0 | High — Rule 3 requires local == CI image; needed before `make verify` is meaningful |
| `harness/__init__.py` | M0 | Medium — Python package init missing |
| `evals/ci_gate.py` | M4 | Low — placeholder only needed |
| `evals/scorers/__init__.py` | M4 | Low — placeholder |
| `sandbox/hyperlight/` | M5 | OK — explicitly deferred |

---

## 2. Orchestrator State (`orchestrator/state.py`)

### ✅ Fully aligned

| PRD field | Scaffold field | Status |
|---|---|---|
| `request` | `request: str` | ✅ |
| `current_agent` | `current_agent: Optional[AgentRole]` | ✅ |
| Gate status + retry counts | `gates: List[GateStatus]` (retry_count on GateStatus) | ✅ cleaner than PRD sketch |
| `story`, `contract`, `schema`, `dag`, `code`, `pr_url` | All present | ✅ |
| `human_approval_pending` | ✅ | — |
| Resume / checkpointer | `PostgresCheckpointer` in graph.py | ✅ |

**One addition to make:** `ContinuumState` needs a `pev_contract` field (Optional) to persist the Plan→Execute→Verify contract per PRD §4.1 (Rule 8: "plans are contracts"). Not currently in scaffold.

---

## 3. LangGraph Graph Shape

### ✅ Supervisor pattern correct

`graph.py` correctly implements:
- Single orchestrator node with `add_conditional_edges`
- All agents route back to orchestrator
- `wait_human` → re-enter orchestrator

### ⚠️ Single developer node vs PRD's 3 parallel dev agents

PRD §6 agents 4/5/6 are **Frontend, Backend, Database** — three separate parallel dev nodes.
Scaffold has one `"developer"` node.

- **M0 verdict:** OK — Handoff brief explicitly says "one Dev agent" for M0.
- **M1 fix needed:** Split `developer` node into `frontend`, `backend`, `database` with parallel fan-out from Planner via Neo4j unblocked-task query.

### ⚠️ Missing: max-3 retry enforcement in graph edges

`state.py` has `GateStatus.retry_count` but `graph.py`'s `_route()` has `# TODO`. The conditional edge logic for `gate_red + retry < 3 → same agent` vs `retry ≥ 3 → wait_human` is not wired. This is M0-critical.

---

## 4. Agent Roster vs PRD §6

| # | Agent | YAML | M0? | Status |
|---|---|---|---|---|
| 0 | Orchestrator | graph.py | ✅ | ✅ |
| 1 | BSA | bsa.yaml | ✅ | ✅ |
| 2 | Architect | architect.yaml | ✅ | ✅ |
| 3 | Planner | planner.yaml | ✅ | ✅ |
| 4 | Frontend | developer.yaml (template) | ✅ | ⚠️ merged into one |
| 5 | Backend | developer.yaml (template) | ✅ | ⚠️ merged into one |
| 6 | Database | developer.yaml (template) | ✅ | ⚠️ merged into one |
| 7 | Security | security.yaml | M1 | ✅ present early |
| 8 | Code Review | ❌ missing | M1 | Expected missing |
| 9 | PR Review | ❌ missing | M1 | Expected missing |
| 10 | Test | ❌ missing | M1 | Expected missing |
| 11 | Infra | ❌ missing | M3 | Expected missing |
| 12 | Memory | ❌ missing | M3 | Expected missing |
| 13 | Evolution Agent | ❌ missing | M5 | Expected missing |

**Action:** For M1, add `code_review.yaml`, `pr_review.yaml`, `test.yaml`, and split `developer.yaml` into `frontend.yaml`, `backend.yaml`, `database.yaml`.

---

## 5. Skills Coverage

### ✅ Present

`create_story`, `emit_contract`, `emit_schema`, `write_code`, `local_verify`, `commit`, `run_sast`, `raise_pr`, `graphrag_query`

### ❌ Missing — M0 Critical

| Skill | Agent that needs it | Why M0 critical |
|---|---|---|
| `write_spec` | BSA (`allowed_skills`) | BSA YAML lists it; missing stub means BSA can't bind it as a tool |
| `write_dag` | Architect (`allowed_skills`) | Architect needs to write DAG to Neo4j; no skill = no DAG = orchestrator can't schedule |
| `query_dag` | Planner (`allowed_skills`) | Planner reads DAG from Neo4j to generate checklist |
| `make_checklist` | Planner (`allowed_skills`) | Planner output skill |

### ❌ Missing — M0/M1 (lower priority)

| Skill | Agent | Milestone |
|---|---|---|
| `web_research` | BSA | M0 (listed in BSA yaml) |
| `scan_deps` | Security | M1 |
| `secret_scan` | Security | M1 |
| `review_diff` | Code Review | M1 |
| `review_pr`, `request_changes` | PR Review | M1 |
| `gen_tests`, `run_tests`, `triage_failures` | Test | M1 |

---

## 6. Architecture Planes vs Code Modules

| Architecture Plane | Code Module | Status |
|---|---|---|
| EXPERIENCE PLANE | `ui/` | ✅ (M2 placeholder) |
| CONTROL PLANE | `orchestrator/` | ✅ |
| AGENT PLANE | `agents/` | ✅ |
| SANDBOX PLANE | `sandbox/` | ✅ |
| RUNTIME PLANE | Config/deploy only | ✅ (no code module needed for M0) |
| KNOWLEDGE PLANE — Neo4j | `graph_db/` | ✅ |
| KNOWLEDGE PLANE — AI Search | `knowledge/` | ⚠️ only `__init__.py`, no `azure_search_client.py` stub |
| INTEGRATION PLANE | `integrations/` | ⚠️ only ADO client; `web_research.py`, `jira_confluence.py` stubs missing |
| OBSERVABILITY PLANE | `telemetry/`, `evals/`, `evolution/` | ✅ placeholders correct for M0 |

---

## 7. PRD 9 Rules — Enforcement Traceability

| Rule | PRD enforcement mechanism | Scaffold location | Status |
|---|---|---|---|
| 1. Never push and pray | `gate_local_verify` before push | `orchestrator/gates.py` | ⚠️ stub, not wired |
| 2. Every stage is a gate | Conditional edges, no skip paths | `orchestrator/graph.py` | ⚠️ conditional edges exist but gate logic not implemented |
| 3. Local == CI | `ci-mirror.Dockerfile` + drift check | `harness/verify.sh` | ❌ Dockerfile missing |
| 4. Max 3 retries | Per-gate counter on state | `orchestrator/state.py` GateStatus.retry_count | ⚠️ field exists, not enforced in _route() |
| 5. Skills are atoms | Versioned skill modules | `skills/{name}/v1.0/skill.py` | ✅ structure correct |
| 6. Deploys are retags | Foundry promotion gates | `deploy/` | ✅ deferred to M4, placeholder correct |
| 7. Pipeline is the product | Harness versioned, models swappable | `pyproject.toml`, agent YAMLs (model_tier) | ✅ |
| 8. Plans are contracts | PEV contract on every transition | `orchestrator/pev.py` (misplaced) | ⚠️ stub exists, misplaced, not integrated |
| 9. Harness is governed | Evolution Agent + regression evals | `evolution/__init__.py` | ✅ deferred to M5, correct |

---

## 8. Neo4j Schema Alignment

PRD §7 node labels vs `graph_db/schema.cypher`:

| PRD Node | Schema | Status |
|---|---|---|
| Feature | ✅ CONSTRAINT | ✅ |
| Story | ✅ CONSTRAINT | ✅ |
| Task | ✅ CONSTRAINT + INDEX | ✅ |
| Component | ✅ CONSTRAINT | ✅ |
| Artifact | ✅ CONSTRAINT | ✅ |
| Episode | ❌ missing | M3 — add for Memory agent |
| Standard / ADR | ❌ missing | M3 |

PRD §7 relationships:
- `DEPENDS_ON` ✅ with constraint
- `HAS_STORY`, `NEEDS`, `PRODUCES`, `GOVERNED_BY`, `ABOUT`, `CAUSED` — ❌ missing from schema

`graph_db/queries.py` Cypher matches PRD §7 scheduling query exactly ✅.

---

## Priority Fix List (before Claude Code handoff)

### 🔴 M0 Blockers

1. **Add 4 missing skill stubs:** `write_spec`, `write_dag`, `query_dag`, `make_checklist`
2. **Wire gate retry logic** in `orchestrator/graph.py` `_route()`: `retry_count < 3 → re-route to same agent`, `≥ 3 → set human_approval_pending = True`
3. **Add `harness/__init__.py`** and **`harness/ci-mirror.Dockerfile`**
4. **Add `pev_contract` field** to `ContinuumState` for Rule 8 compliance

### 🟡 Pre-M1 Fixes

5. Move `pev.py` → `harness/pev.py` (PRD §14)
6. Move `permissions.py` → `harness/permissions.py` (PRD §14)
7. Add `knowledge/azure_search_client.py` stub
8. Add `integrations/web_research.py` and `integrations/jira_confluence.py` stubs
9. Add remaining Neo4j relationships to schema.cypher
10. Split `developer.yaml` → `frontend.yaml`, `backend.yaml`, `database.yaml`

### 🟢 Expected / Deferred (no action)

- M1 agents (code_review, pr_review, test) — intentionally absent
- M3+ skills (write_episode, graphrag complex, etc.) — intentionally deferred
- Hyperlight / M5 items — intentionally deferred

