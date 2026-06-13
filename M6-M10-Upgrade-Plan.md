# Continuum M6–M10 — Refined Plan (reconciled against the real codebase)

## Context

An M6→M10 upgrade document (Spine vs Frontier framing) was drafted against an
idealized picture of Continuum that **does not match the actual M5 codebase** in
several load-bearing places. This document is the corrected, executable version:
the original intent is preserved, but every file name, gate name, event, and UI
component has been reconciled against the source. No implementation is included
here — M6 is the first dispatchable item when implementation begins.

Frontend direction: **adapt to the existing UI** (extend the current `App.tsx` +
`RunList`/`GateInbox`/`ArtifactViewer` structure, keep the Unicode-symbol icon
convention, add **no** new UI dependencies — no `lucide-react`, no router).

The single biggest correction: the original plan's frontend file names
(`Gate1View`, `Gate2View`, `Results`, `IntentView`) and its icon library
(`lucide-react`) **do not exist**; they are mapped here to the real files. M6 is
specified in execution-ready detail; M7 is corrected; M8–M10 are corrected notes
(Frontier, not started).

---

## Reconciliation — where the original document diverges from the code

Verified by reading the source. Original assumption shown for contrast.

| Area | Original document assumed | Reality in code |
|---|---|---|
| Gate names | `gate_lint`, `gate_typecheck`, `gate_test`, `gate_contract_validate`, `gate_sast` | `orchestrator/gates.py` has **`gate_local_verify`** (ruff→mypy→pytest→py_compile, one combined gate), **`gate_sast`**, **`gate_contract_validate`**. No separate lint/typecheck/test gates. GateStatus names written to state: `local_verify`, `security_sast`. |
| Execution engine | LangGraph (`graph.py`) drives runs | The live API runs **`_execute_pipeline()` over an in-memory `_RUNS: Dict[str, ContinuumState]`** in `api/main.py`. `ContinuumGraph` in `graph.py` exists but is not the path the API/UI exercises. **M6 changes land primarily in `_execute_pipeline()` + the API**, keeping `graph.py` field-consistent so it doesn't break. |
| Run status values | `running/waiting_gate/blocked/returned/done` | `api/main.py::_classify()` returns `running/awaiting_approval/complete/failed`. Must be reconciled (M6 Task 1). |
| `started_at`/`completed_at` | new `datetime` fields | **Already exist** on `ContinuumState` as `Optional[float]` (UNIX ts). Reuse them; `duration_s = completed_at - started_at`. Do **not** re-add as datetime. |
| Event types | "add event types" to an enum | Events are **plain dicts** (`{"event_type", "agent", "run_id", "data", "timestamp"}`) emitted via the `event_bus` singleton in `orchestrator/events.py`. "New event type" = emit a new `event_type` string. No enum. Existing: `agent_start`, `agent_complete`, `gate_green`, `gate_red`, `gate_retry`, `human_gate_pending`, `human_gate_resolved`, `run_complete`. |
| Cost tracking | accumulate token usage | **None exists.** LangChain handles usage opaquely. Offline path makes **no LLM call**, so `cost_usd` must use a fixed per-agent estimate offline. |
| UI components | `Gate1View.tsx`, `Gate2View.tsx`, `Results.tsx`, `IntentView.tsx` | **None exist.** Real `ui/src/components/`: `ActivityStream.tsx`, `AgentGraph.tsx`, `ArtifactViewer.tsx`, `GateInbox.tsx`, `RunList.tsx`. Layout = one `App.tsx` (left `RunList`, center `AgentGraph` rail, right panel = `GateInbox` + Activity/Artifact tabs). Approvals live in **`GateInbox.tsx`** (`POST /run/{id}/resume?approved=`). |
| Icons | `lucide-react` glyphs | **No icon library.** UI uses Unicode (`✓ ✗ ↺ ⏸ ■ ▶ ·`). Keep this — map plan icons to glyphs. |
| API client | unspecified | `ui/src/lib/api.ts` (thin fetch wrappers); `ui/src/hooks/useSSE.ts` (EventSource on `/events/{id}`); `ui/src/types.ts`. `App.tsx` polls `GET /runs` every 5s. **New top-level API paths must be added to the Vite proxy** in `vite.config.ts` (`/run` prefix covers `/run/{id}/reject`; `/runs` covers `/runs/{id}/evidence` — confirm during impl). |
| `evals/evidence_stack.py` | new file | Confirmed absent — new file. `evals/scorers/deterministic.py` has the 6 weighted scorers; `ci_gate.py` compares to `results/baseline.json` (2pp regression → exit 1). |
| Verify script convention | — | All `scripts/verify_*.py` share: UTF-8 `io.TextIOWrapper` stdout wrap, `sys.path.insert(0, repo_root)`, a `_check(name, passed, detail)` helper appending to a module list, `passed/total` summary, `sys.exit(asyncio.run(main()))`. New scripts must match. |

---

# M6 — Work Queue UI + Evidence Stack + Run Metrics (executable spec)

Goal: the UI becomes a multi-run triage console, and completed runs show a
6-layer Evidence Stack plus cost + lead-time, with two newly surfaced run states
(`blocked`, `returned`).

## Task 1 — State + cost accounting

**`orchestrator/state.py`** — add to `ContinuumState`:
- `run_status: str = "running"` — allowed: `running`, `waiting_gate`, `blocked`,
  `returned`, `done`, `failed` (keep `failed` as the existing terminal error
  state; it's a 6th value the original omitted).
- `cost_usd: float = 0.0`
- `reject_reason: Optional[str] = None`
- **Reuse** existing `started_at` / `completed_at` (floats).

**`api/main.py::_classify()`** — prefer `state.run_status` when set, falling back
to current derivation. Map legacy strings so existing clients still work:
`awaiting_approval`→`waiting_gate`, `complete`→`done`. Set `run_status` explicitly
at the transitions in `_execute_pipeline()` (start→`running`; entering a human
gate→`waiting_gate`; retry exhaustion→`blocked`; normal finish→`done`) and in the
new reject endpoint (→`returned`).

**`orchestrator/agent_runner.py::run_agent()`** — accumulate cost after each agent
run into `state.cost_usd`:
- Offline (no model resolved): fixed estimate per agent run
  (`_OFFLINE_COST_PER_AGENT = 0.02`) so the field is always populated.
- Live: if the LangChain response exposes `response_metadata["token_usage"]`,
  multiply prompt/completion tokens by a small pricing table keyed on
  `model_tier`; otherwise fall back to the fixed estimate.
- Single helper `_accrue_cost(state, spec, response)` fed by both `_run_llm` and
  `_run_offline`. Must not break the offline suite — it only ever adds a float.

## Task 2 — New events

**`orchestrator/events.py`** — no enum; emit two new `event_type` strings
(document their shape in a comment near the existing emitters):
- `run_blocked` — from `_execute_pipeline()` when a gate is red and
  `retry_count >= 3`. `data`: `{gate_name, error_message}`.
- `run_returned` — from the new reject endpoint. `data`: `{gate, reason}`.

Add matching entries to the UI `ActivityStream.tsx` `EVENT_STYLES` map
(`run_blocked` → `▲` rose, `run_returned` → `↩` rose).

## Task 3 — Reject path, escalate-resolve, evidence endpoint

**`api/main.py`** (in-memory `_RUNS` model):
- `POST /run/{run_id}/reject` — body `{gate: "story_review"|"design_review", reason: str}`.
  Sets `run_status="returned"`, `reject_reason=reason`, emits `run_returned`, and
  re-routes the in-memory walk back to the relevant stage (clear
  `story_approved`/`design_approved` and restart `_execute_pipeline()` from BSA or
  Architect). Mirror the existing `resume_run()` structure.
- `POST /run/{run_id}/escalate-resolve` — resets the blocking gate's
  `retry_count = 0`, `status = "pending"`, clears `run_status` back to `running`,
  re-drives the developer chain — reusing the logic in `resume_run()`'s
  `local_verify` branch.
- `GET /runs/{run_id}/evidence` — returns `build_evidence_stack(state)`.
- `GET /runs` — extend each row with `run_status`, `cost_usd`, `duration_s`
  (`completed_at - started_at`, or now − started_at if running), and `stage_idx`
  (index of `current_agent` within `[bsa, architect, planner, database, backend,
  frontend, security, memory]`).

**`evals/evidence_stack.py`** (new) — `build_evidence_stack(state) -> list[dict]`
returning `[{layer, status, detail}]` for 6 layers mapped onto **real** gates:
1. **Build / compile** — from the `local_verify` GateStatus (ruff+mypy portion);
   `pass` iff green, detail = its message.
2. **Regression suite** — also from `local_verify` (pytest portion); same gate,
   distinct sub-label ("pytest"). (Combined gate in reality; surfaced as two
   layers with different detail text.)
3. **Acceptance-criteria check** — from the contract-validate result
   (`gate_contract_validate` output recorded by `_run_post_gates` for Architect).
4. **Scope conformance** — **stub `pass=True`** for M6 (M7 wires this to
   `mapping_fidelity.exact_match`).
5. **Lint + secret scan** — from the `security_sast` GateStatus.
6. **Human review** — from approval history: `story_approved`, `design_approved`,
   `merge_approved` flags + `human_gate_resolved` events.

Offline-safe (pure function of `state`, no network), per the
`evals/scorers/deterministic.py` convention.

## Task 4 — UI (adapt to existing structure, no new deps)

**`ui/src/lib/api.ts`** — add `rejectRun(runId, gate, reason)`,
`escalateResolve(runId)`, `getEvidence(runId)`; extend the `listRuns()` return
type. **`ui/src/types.ts`** — extend `RunSummary` with `run_status`, `cost_usd`,
`duration_s`, `stage_idx`; add `EvidenceLayer` type.

Components (`ui/src/components/`), reusing existing Tailwind tokens + Unicode
glyphs:
- **`WorkQueue.tsx`** (new) — landing list, extending `RunList`'s role (keep its
  new-run form). Sections: "Waiting on you" (`run_status==="waiting_gate"`) and
  "All runs", each row a status badge: running=sky, waiting=amber, blocked=rose,
  returned=rose, done=emerald (reuse `RunList.tsx`'s `STATUS_COLORS` pattern).
- **`Spine.tsx`** (new) — per-run vertical stage tracker; primary nav when a run
  is open. **`AgentGraph.tsx` stays** as a secondary "engineer view" toggle
  (additive). Stage glyphs: done `✓` / running `⟳` (animate-pulse) / waiting `⏸` /
  blocked `▲` / returned `↩` / pending `○`.
- **`Blocked.tsx`** (new) — right panel when `run_status==="blocked"`: failing
  gate + sensor output + "Send back to Implementation" → `escalateResolve()`.
- **`Returned.tsx`** (new) — when `run_status==="returned"`: shows `reject_reason`
  + link back to the queue.
- **`EvidenceStack.tsx`** (new) — 6 rows from `getEvidence()`, each PASS/FAIL with
  the sub-labels ("independent ground truth", "end to end", etc.).
- **Run metrics** — no `Results.tsx` exists; add **`RunMetrics.tsx`** (or a
  section in `ArtifactViewer.tsx`) for completed runs: First-pass (`all gates
  retry_count===0`), Lead time (`duration_s`), Model cost (`cost_usd`, `$0.42`).
- **`GateInbox.tsx`** (edit) — its "Reject" button currently calls
  `resumeRun(approved=false)`; change it to open a reason textarea and call
  `rejectRun(runId, gate, reason)`.
- **`App.tsx`** (edit) — `WorkQueue` is the landing view; opening a run shows
  `Spine` + the existing right panel; conditionally render `Blocked`/`Returned` by
  `run_status`; keep the `AgentGraph` toggle.

## Done when (M6)
- `make verify-offline` → 11/11 + 3/3, plus `verify-m3` 6/6, `verify-m5` 6/6,
  `python evals/ci_gate.py` exit 0 — all still pass (offline path intact).
- Two concurrent requests both appear live in the Work Queue with correct status.
- A run that exhausts `local_verify` retries renders `Blocked.tsx` with sensor
  output; "Send back to Implementation" re-drives it.
- Rejecting at story/design gate renders `Returned.tsx` and records
  `run_status="returned"` + `reject_reason`.
- A completed run shows the 6-row Evidence Stack + First-pass / Lead time / Cost.
- **Recommended:** add `scripts/verify_m6_workqueue.py` asserting offline:
  `cost_usd > 0` after a run, `run_status` transitions, reject→`returned`,
  retry-exhaustion→`blocked`, `build_evidence_stack()` returns 6 layers. Wire a
  `verify-m6` Makefile target.

---

# M7 — Mapping Fidelity (D11 Scope-Guard) — corrected

Intent: human supplies `business_mappings` (e.g. `BR→Branch`) at intent time;
after the code-producing agents run, a new gate proves the generated code uses
exactly those mappings — nothing invented. A mismatch **escalates to human
(`run_blocked`), never auto-retries.**

- "Implementation agent's diff" = the **DATABASE → BACKEND → FRONTEND** chain's
  output in `state.code` (there is no single "Implementation" role).
- **`api/main.py` `POST /run`** accepts optional `business_mappings:
  list[{code,label}]`; store on `state.business_mappings`.
- **`orchestrator/state.py`** — add `business_mappings: list[dict] = []` and
  `mapping_fidelity: Optional[dict] = None`.
- **`skills/scope_guard/v1.0/skill.py`** (new) — single `async def scope_guard(...)`,
  offline-safe, file-path loaded. Scans `state.code` for map/dict/enum-like
  literals containing any supplied code; returns `{supplied, found, exact_match,
  extra_in_code, missing_in_code}`.
- **`orchestrator/gates.py`** — new `gate_scope_conformance`: green iff
  `exact_match and not extra_in_code`. Recorded via `update_gate_status()`.
- **`orchestrator/graph.py` + `api/main.py::_execute_pipeline()`** — wire the gate
  after FRONTEND / before `merge_review`; on red → emit `run_blocked` and land in
  M6's `Blocked` state (**not** auto-retry).
- **`evals/evidence_stack.py`** — layer 4 now reads real
  `state.mapping_fidelity.exact_match`.
- **UI** — business-mappings table in the new-run form (`WorkQueue`/`RunList`)
  with the D11 shield note; new **`MappingFidelity.tsx`** (two-column
  supplied-vs-found, exact-match pill / mismatch warning) in the `GateInbox`
  review area and run summary.
- **`scripts/verify_m7_scope_guard.py`** (new, matches convention) — 2/2: case A
  exact-match → green; case B offline stub injects an extra mapping → red →
  `Blocked` with the specific reason. Add `verify-m7` Makefile target.

---

# Frontier (M8–M10) — corrected notes, not started

Sequenced after M6+M7 land.

- **M8 — Two-Layer Repo Split (D33).** Layer 1 (process:
  orchestrator/agents/skills/evals/UI) vs a Layer 2 `.pdlc/` directory in a target
  app. The offline scaffold today is **FastAPI + React** (see
  `skills/write_code/v1.0/skill.py` `_build_scaffold`), so a FastAPI+React sample
  target keeps the sandbox single-language. Graduation: M6+M7 proven.
- **M9 — MAF Harness Pilot.** Wrap one agent (Backend) on Microsoft Agent
  Framework. Current reality: agents resolve to **LangChain `AzureChatCompletions`**
  via `resolve_model()` with a hand-rolled tool-call loop (`_run_llm`,
  `MAX_TOOL_ITERS=5`); MAF would replace that loop for one agent on a branch.
  Parallel-safe after M7.
- **M10 — ASSERT / Rubric Eval Integration.** Replace `evals/scorers/judge.py`
  (bespoke Azure 1–5 judge + heuristic fallback) with ASSERT specs for Rules 1–9 +
  the M7 scope invariant; keep the `pass^k` runner; wire `evidence_stack.py` layers
  to per-trial results; emit OTel from `orchestrator/events.py`. Graduation: M6+M7
  merged (cost + mapping-fidelity signals go into the specs from day one).

Sequencing: **M6 → M7 → {M8 structural, M9 parallel-safe, M10 specs-reuse}.**

---

## Verification (for whoever executes M6/M7 later)

Non-negotiable offline suite, must stay green before every commit:
```
python scripts/verify_agent_core.py    # 11/11
python scripts/verify_m0_loop.py       # 3/3
python scripts/verify_m3_learning.py   # 6/6
python scripts/verify_m5_evolution.py  # 6/6
python evals/ci_gate.py                # exit 0 (no >2pp regression vs baseline.json)
```
Plus the new milestone scripts (`verify_m6_workqueue.py`, then
`verify_m7_scope_guard.py` → 2/2). Manual end-to-end: `make run`, submit two
requests, confirm both live in the Work Queue; force a persistent-red run into
`Blocked`; reject at a gate into `Returned`; open a completed run to see the
6-row Evidence Stack + cost/lead-time. All new fields/events/skills must work
**fully offline** (no Azure/Neo4j/Postgres).
