# Continuum — Progress Log

A dated, chronological record of what shipped, why, and how it was verified.
**Newest entries on top.** Every commit that changes behaviour appends an entry
here (see the "Progress log discipline" section in `CLAUDE.md`).

Entry format:
```
## YYYY-MM-DD — <short title> (<milestone>)
**Branch:** <branch>  ·  **Commit:** <short sha or "pending">
**What:** one-paragraph summary of the change.
**Files:** the load-bearing files touched.
**Verification:** the exact checks run and their results.
**Notes / follow-ups:** anything the next session should know.
```

---

## 2026-06-13 — M6: Work Queue UI + Evidence Stack + run metrics (M6)
**Branch:** `claude/previous-session-plan-xpz609`  ·  **Commit:** pending

**What:** Implemented M6 from `M6-M10-Upgrade-Plan.md`. The pipeline now tracks a
first-class run lifecycle (`running → waiting_gate → blocked / returned → done /
failed`), accrues a per-run model cost and lead-time, exposes a 6-layer Evidence
Stack, and the React UI became a multi-run triage console (Work Queue + Spine +
Blocked/Returned panels + Evidence/Run-metrics). All changes are offline-safe.

**Files:**
- `orchestrator/state.py` — added `run_status`, `cost_usd`, `reject_reason`.
- `orchestrator/agent_runner.py` — `_accrue_cost()` + `_attach_usage()`; cost
  accrues per agent (fixed estimate offline, token-priced when live).
- `orchestrator/events.py` — documented new `run_blocked` / `run_returned` events.
- `api/main.py` — `_classify()` reconciled to the M6 vocabulary; `_run_summary`,
  `_duration_s`, `_stage_idx`, `_mark_blocked` helpers; new endpoints
  `POST /run/{id}/reject`, `POST /run/{id}/escalate-resolve`,
  `GET /runs/{id}/evidence`; removed a dead duplicate `resume_run` definition.
- `evals/evidence_stack.py` (new) — `build_evidence_stack(state)`, pure/offline.
- `ui/src/components/` — new `WorkQueue`, `Spine`, `Blocked`, `Returned`,
  `EvidenceStack`, `RunMetrics`; edited `GateInbox` (return-with-reason),
  `ActivityStream` (new event styles), `App.tsx`; removed superseded `RunList`.
- `ui/src/{types.ts, lib/api.ts}` — M6 types + API client methods.
- `scripts/verify_m6_workqueue.py` (new) + `Makefile` `verify-m6` target.
- `README.md` — M6 section, badges, milestone timeline, verification matrix.

**Verification (offline / thin environment — no Azure/Neo4j/Postgres):**
- `python scripts/verify_agent_core.py` → 11/11 PASS
- `python scripts/verify_m0_loop.py` → 3/3 PASS
- `python scripts/verify_m3_learning.py` → 6/6 PASS
- `python scripts/verify_m5_evolution.py` → 6/6 PASS
- `python scripts/verify_m6_workqueue.py` → 6/6 PASS
- `python evals/ci_gate.py` → exit 0 (no regression vs baseline)
- `cd ui && npm run build` → tsc + vite build clean (210 modules)

**Notes / follow-ups:**
- Reconciliation confirmed: the live API path is `_execute_pipeline()` (runs a
  single `developer` stage, not the DB→BE→FE split), and human gates only fire on
  `local_verify` exhaustion. `run_status` transitions are wired into that path.
- `reject` parks the run in `returned` (not auto-restarted) so the status stays
  stable for the Work Queue; the operator resubmits.
- Cleaned pre-existing repo lint debt (ruff: 31→0) so `local_verify` is green when
  ruff is installed. Pre-existing mypy type errors remain out of scope — the
  canonical offline path is a thin env where mypy/pytest are absent and the gate
  falls back to `py_compile`.
- **Next:** M7 (Mapping Fidelity / Scope-Guard) — wire Evidence Stack layer 4 to
  real `state.mapping_fidelity.exact_match`. See `M6-M10-Upgrade-Plan.md`.
