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

## 2026-06-17 — P0.1: Durable Execution — run persistence layer (P0.1)
**Branch:** `feature/p0-durable-execution`  ·  **Commit:** pending
**What:** Adds `graph_db/run_store.py` `RunStore` — a two-tier persistence layer for
`ContinuumState`. Tier 1 (live): asyncpg-based Postgres upserts after every human-gate
suspension and at pipeline completion, so runs survive a process restart. Tier 2
(offline default): the module-level `_MEMORY` dict, which `api/main._RUNS` is now an
alias of — same object, no copy, no behaviour change for the verify suite. On startup,
`restore_active()` reloads non-terminal runs from Postgres. Any Postgres error degrades
silently to in-memory. Also fixes `ContinuumGraph._run_agent()` to create a per-run
`AgentContext` (was using a shared context, unsafe for concurrent runs).
**Files:** `graph_db/run_store.py` (new), `api/main.py` (RunStore wiring), `orchestrator/graph.py` (per-run context fix), `scripts/verify_p0_durable_execution.py` (new), `Makefile`, `CLAUDE.md`, `PROGRESS.md`.
**Verification:**
- verify_agent_core: 11/11 ✅
- verify_m0_loop: 3/3 ✅ (unchanged — _execute_pipeline and _RUNS behaviour identical)
- verify_m11_spec_registry: 4/4 ✅ · verify_m12_compliance: 3/3 ✅
- verify_m13_state_machine: 6/6 ✅ · verify_p1_gate_independence: 6/6 ✅
- **verify_p0_durable_execution: 4/4 ✅**
- evals/ci_gate.py: exit 0 ✅
**Notes:** Execution path switch (use `ContinuumGraph.run()` instead of `_execute_pipeline`) is the P0.1 follow-up — requires LangGraph checkpointer suspend/resume and resolves the mid-run state gap. `POSTGRES_DSN` env var activates the Postgres tier.

## 2026-06-14 — P1.1: Gate Independence (resolves OQ-3) (P1.1)
**Branch:** `feature/p1-gate-independence` (stacked on `feature/m13-state-machine`)  ·  **Commit:** pending

**What:** Implemented P1.1 from the production-readiness track — splitting the
combined `local_verify` gate into three INDEPENDENT gates (`gate_lint`,
`gate_typecheck`, `gate_test`), each with its own pass/fail and its own evidence
record. This makes the Evidence Stack's "6 independent signals" literally true
(**resolves OQ-3**) and makes M12's compliance claims defensible. Single-run design:
`gate_local_verify_split()` runs each gate once and is the source of truth; the
DEVELOPER path records all three `GateStatus`es plus a derived composite
`local_verify` (so the live retry loop, graph routing, and M13 quality gate — all of
which key on `local_verify` — are unchanged). The Evidence Stack now reads layer 1
(build = lint+typecheck) and layer 2 (regression = test) from *different* signals
when the split gates exist, falling back to `local_verify` for pre-P1.1 states
(byte-unchanged). Each gate keeps its `py_compile` offline fallback. Per-gate retry
*budgets* in the live loop remain a follow-up tied to P0.1.

**Files:**
- `orchestrator/gates.py` — new `gate_lint` / `gate_typecheck` / `gate_test` /
  `gate_local_verify_split`; `gate_local_verify` refactored to the composite
  aggregate; `_pycompile` helper. `_python_targets` unchanged.
- `orchestrator/agent_runner.py` — DEVELOPER branch runs the split once, records the
  three sub-gates + the composite (single tool run, no double-execution).
- `evals/evidence_stack.py` — layers 1 & 2 read independent gates when present
  (`_combine_status` / `_combine_detail`), else fall back to `local_verify`; sublabels
  updated; 6-layer shape and keys unchanged.
- `scripts/verify_m0_loop.py` — patches `gate_local_verify_split` (the new source of
  truth) instead of the composite; same retry/escalation behaviour, still 3/3.
- `scripts/verify_p1_gate_independence.py` (new, 6/6) + `Makefile` `verify-p1`.
- `CLAUDE.md` / `README.md` — non-negotiable rules, commands, Gate-system section
  rewrite, P1.1 invariant, OQ-3 marked resolved, badges, verification matrix,
  production-readiness P1.1 → shipped.

**Verification:** all suites green, zero credentials:
- M0–M13 suites all pass (notably `verify_m0_loop` 3/3 after the patch-target change,
  `verify_m6_workqueue` 6/6, `verify_m10_assert` 6/6 — Evidence Stack shape intact)
- `verify_p1_gate_independence.py` → **6/6** · `evals/ci_gate.py` → exit 0 · `import api.main` OK.

**Notes / follow-ups:** This is the offline-verifiable slice of P1. The remaining P0
work (durable execution, sandbox, auth/tenancy) and P1.2 observability touch the live
path / external services and are not offline-verifiable. Recommended next: **P0.3
auth/tenancy** (the gate for real-client use) or **P0.1 durable execution** (which is
also where the M13 policy engine becomes the live gating mechanism).

---

## 2026-06-14 — M13: 15-State SDLC Machine + Policy Engine (M13)
**Branch:** `feature/m13-state-machine` (stacked on `feature/m12-compliance`)  ·  **Commit:** pending

**What:** Implemented M13 from `Continuum-PRD-v3.0.md` §8 — the first Frontier
milestone (unblocked now M11+M12 shipped). The SDLC lifecycle is now an explicit,
policy-governed graph instead of being implicit in routing code. `state_machine.py`
**declares** the 15 states (`NEW → … → CLOSED`), each with entry criteria
(`required_artifacts` + `quality_gates`), allowed `forward`/`returns` edges, and the
four named human gates (`TRANSITION_GATES`: G1 NEW→EPIC_APPROVED, G2 ARCH_READY→
IMPL_READY, G3 RELEASE_READY→DEPLOYED, G4 IN_PRODUCTION→IN_PROGRESS incident return).
`policy_engine.py` **enforces** it: `can_transition(artifact, from, to) -> (ok,
reason)` is a pure predicate (edge → gate → artifacts → quality gates), and
`advance()` mutates `lifecycle_state` only when permitted. Return/exception edges
(tests-fail, security-findings, prod-incident) are first-class. The live
`_execute_pipeline()` path is **deliberately unchanged** — `lifecycle_state` is the
new `ContinuumState` field (system of record) and the API surfaces a read-only value
via `derive_lifecycle_state()`; existing story/design/merge approvals map to G1/G2/G3.
Wiring the policy engine as the live *gating* mechanism (replacing `_route()`) is a
follow-up tied to P0.1.

**Files:**
- `orchestrator/state_machine.py` (new) — `SDLCState` (15), `Gate` (G1–G4),
  `GATE_INFO`/`GATE_APPROVAL_FIELD`, `StateDefinition`, `STATE_MACHINE`,
  `TRANSITION_GATES`, introspection helpers (`all_states`, `forward_path`,
  `gate_for_transition`, …) and `derive_lifecycle_state`. Pure data; zero orchestrator
  imports so `state.py` can import `SDLCState` cycle-free.
- `orchestrator/policy_engine.py` (new) — `can_transition`, `advance` (pure, read
  artifact via getattr).
- `orchestrator/state.py` — `lifecycle_state: SDLCState = NEW` + `incident_approved`.
- `api/main.py` — read-only `lifecycle_state` in the run serializer via a guarded
  `_lifecycle_state()` helper.
- `scripts/verify_m13_state_machine.py` (new, 6/6) + `Makefile` `verify-m13`.
- `CLAUDE.md` / `README.md` — non-negotiable rules, commands, state-machine
  architecture section, M13 invariant, badges, timeline, verification matrix
  (M13 moved roadmap → shipped; only M14 remains roadmap).

**Verification:** all suites green, zero credentials:
- M0–M12 suites all pass (`verify_agent_core` 11/11 … `verify_m12_compliance` 3/3)
- `verify_m13_state_machine.py` → **6/6** · `evals/ci_gate.py` → exit 0 (no regression)
- `import api.main` + `orchestrator.{state,state_machine,policy_engine}` OK.

**Notes / follow-ups:** The live pipeline is unchanged this milestone (lifecycle is
surfaced, not yet enforced) — making `policy_engine` the live gating path (refactor
`_route()` / `_execute_pipeline`) is the natural P0.1 companion. Next: **M14 MAF
Graduation** (not offline-verifiable) or the **P0** production track.

---

## 2026-06-14 — M12: Compliance Report (M12)
**Branch:** `feature/m12-compliance` (stacked on `feature/m11-spec-registry`)  ·  **Commit:** pending

**What:** Implemented M12 from `Continuum-PRD-v3.0.md` §8 — the second Spine
milestone, and **packaging, not new capability**. `api/compliance.py`
`build_compliance_report(run_id, state, events=None)` assembles an auditor-readable
artifact from data that already exists across the run state and event bus: 8
always-present sections (run metadata, spec, business mappings, gate decisions,
the 6-layer Evidence Stack, audit trail, harness/model versions, and a boolean
compliance-assertions checklist). The `spec` section prefers the M11 Registry and
falls back to work-item state. Honesty is built in: a blocked/returned run yields a
*valid* report whose `compliance_assertions.passed` is truthfully `False`, and any
absent data is an explicit `null`/`[]` carrying a `_missing_reason`, enumerated in
`report["missing"]` — never silently omitted (`complete == missing == []`). Two
endpoints: `GET /runs/{run_id}/compliance-report` (JSON) and `…/compliance-report.html`
(`render_compliance_html`). Pure/offline: audit trail from the in-memory event bus,
`git describe` + `config` are best-effort lazy lookups; reuses `build_evidence_stack`.

**Files:**
- `api/compliance.py` (new) — `build_compliance_report`, `render_compliance_html`,
  `SECTION_ORDER`, the eight `_section_*` builders, `_collect_missing`, and
  best-effort `_harness_version` / `_model_config` helpers.
- `api/main.py` — `GET /runs/{run_id}/compliance-report` (+ `.html`); import +
  `HTMLResponse`.
- `scripts/verify_m12_compliance.py` (new, 3/3) + `Makefile` `verify-m12` target.
- `CLAUDE.md` / `README.md` — non-negotiable rules, commands, Compliance Report
  architecture section, M12 invariant, badges, timeline, verification matrix
  (M12 moved roadmap → shipped).

**Verification:** all suites green, zero credentials:
- `verify_agent_core.py` (11/11) · `verify_m0_loop.py` (3/3) · `verify_m3_learning.py` (6/6)
- `verify_m5_evolution.py` · `verify_m6_workqueue.py` · `verify_m7_scope_guard.py` (2/2)
- `verify_m8_repo_split.py` (3/3) · `verify_m9_maf_pilot.py` (6/6) · `verify_m10_assert.py` (6/6)
- `verify_m11_spec_registry.py` (4/4) · `verify_m12_compliance.py` → **3/3**
- `evals/ci_gate.py` → exit 0 (no regression) · `import api.main` OK.

**Notes / follow-ups:** Approver identity + approval timestamps are explicit nulls
with a reason (`pending auth, P0.3`) — they become real once auth lands. The audit
trail reads the in-memory event bus; the durable-Postgres source is a P-track
concern. Branch is stacked on M11 (PR #16) — merge M11 first, then this PR shows a
clean M12-only diff. Next: **M13 15-State Machine** (Frontier, gated on M11+M12,
now both shipped) or the **P0** production track.

---

## 2026-06-14 — M11: Spec Registry (M11)
**Branch:** `feature/m11-spec-registry`  ·  **Commit:** pending

**What:** Implemented M11 from `Continuum-PRD-v3.0.md` §8 — the first Spine
milestone. A spec no longer dies with its run: the **Spec Registry** is a
persistent, versioned, append-only store of the agreed spec per *component*
(a deterministic slug). On a run, the BSA retrieves the prior current spec for the
component **before** drafting (`query_spec_registry`), then persists this run's spec
as a new version (`write_spec_registry`), recording a `SUPERSEDES` link + reason when
the body materially differs. The M7 scope-guard gained a Registry-conformance branch:
a run whose spec drifts from the Registry's current spec **without** a recorded
supersession is red (the branch only fires when a prior spec exists, so M0–M10 runs
are byte-unchanged). New read-only endpoints expose the version chain. Fully
offline-safe: a module-level `_IN_MEMORY_SPECS` store gives cross-run persistence with
no Neo4j; the live Neo4j path runs only when a *connected* driver is passed.

**Files:**
- `graph_db/spec_registry.py` (new) — `write_spec`, `get_current_spec`,
  `get_spec_history`, `mark_superseded`, `list_components`, `specs_match` (shared
  conformance predicate), `_is_live`, `_reset_registry`. Three-tier fallback mirrors
  the M3 driver; live Cypher guarded so it never breaks offline.
- `orchestrator/component.py` (new) — `component_slug(request, story)`, deterministic.
- `skills/query_spec_registry/v1.0/skill.py` + `skills/write_spec_registry/v1.0/skill.py`
  (new) — distinct from the existing `write_spec` formatter skill; offline-safe, never raise.
- `orchestrator/agent_runner.py` — BSA offline path retrieves+persists the Registry
  spec; `apply_agent_output` stores `component`, `registry_specs`,
  `registry_current_before`, `spec_superseded`.
- `orchestrator/state.py` — four M11 fields.
- `orchestrator/gates.py` — `gate_scope_conformance` restructured: M7 mapping check +
  M11 Registry-conformance check; M7 messages/behaviour preserved exactly.
- `api/main.py` — `GET /specs`, `GET /specs/{component}`; BSA artifact view + state
  serializer surface M11 fields; step-2.5 guard widened to fire on a prior spec.
- `agents/bsa.yaml` — two new allowed_skills.
- `scripts/verify_m11_spec_registry.py` (new) + `Makefile` `verify-m11` target.
- `CLAUDE.md` / `README.md` — non-negotiable rules, commands, Spec Registry
  architecture section, M11 invariant, badges, timeline, verification matrix
  (M11 moved from roadmap → shipped).

**Verification:** all suites green, zero credentials:
- `verify_agent_core.py` → ALL PASS (11/11) · `verify_m0_loop.py` → ALL PASS (3/3)
- `verify_m3_learning.py` → 6/6 · `verify_m5_evolution.py` → OK · `verify_m6_workqueue.py` → OK
- `verify_m7_scope_guard.py` → 2/2 · `verify_m8_repo_split.py` → 3/3
- `verify_m9_maf_pilot.py` → 6/6 · `verify_m10_assert.py` → 6/6
- `verify_m11_spec_registry.py` → **4/4** · `evals/ci_gate.py` → exit 0 (no regression)
- End-to-end smoke: two BSA runs on the same request → run 2 grounded on run 1's v1,
  filed v2; `list_components` shows current_version=2.

**Notes / follow-ups:** Identical specs across runs still create a new (non-superseding)
version — intentional per-run audit trail; supersession only fires on material diff.
Live Neo4j reads from the API endpoints require a *connected* driver (offline uses the
module store) — wiring a connected driver is a P-track concern. Next Spine milestone:
**M12 Compliance Report**, which can reuse `build_evidence_stack` + the Registry spec.

---

## 2026-06-14 — PRD v3.0 adoption + M11–M14 / P0–P2 roadmap docs (docs)
**Branch:** `feature/prd-v3-roadmap-docs`  ·  **Commit:** pending

**What:** Adopted **PRD v3.0** (supersedes v2.2) and documented the forward roadmap.
Docs-only — no behaviour change, no new milestone code. v3.0 reframes the project:
M0–M10 are the *delivered baseline*; M11–M14 (Spec Registry, Compliance Report,
15-State Machine, MAF Graduation) are a sequenced **feature track** (Spine M11–M12
shippable now, Frontier M13–M14 gated on them). Also folded in a user-requested
**production-readiness track (P0–P2)**: five gaps between "sound POC" and "system a
bank can run" — P0 durable execution / sandbox hardening / auth+tenancy, P1 gate
independence / observability / M12, P2 M11 / M13 / Hyperlight / concurrency. The
honest reframe carried into the docs: P0 hardening (esp. P0.1 wiring `ContinuumGraph`
+ Postgres checkpointer as the live path, retiring in-memory `_RUNS`) precedes the
feature milestones; OQ-3 (Evidence Stack layers 1+2 are the same `gate_local_verify`)
is resolved only by P1.1's gate split; M14/Hyperlight are explicitly not
offline-verifiable. The non-negotiable verify list was deliberately **not** extended
with M11–M14 scripts (they don't exist yet).

**Files:**
- `Continuum-PRD-v3.0.md` (new) — full PRD v3.0 verbatim; `Continuum-Agentic-SDLC-PRD.md`
  (v2.2) retained as superseded history.
- `README.md` — milestone badge `M0–M10_Shipped` + new `Roadmap` badge; timeline
  extended with 🔜 M11–M14 rows; new "🧭 Roadmap" section (feature track table +
  production-readiness P0–P2 table + honesty notes); verification matrix gains a
  "Planned (M11–M14)" sub-list clearly marked *not implemented*; doc table adds the
  v3.0 row (v2.2 relabelled superseded); doc badge → PRD v3.0.
- `CLAUDE.md` — PRD v3.0 pointer in intro; new "Roadmap (M11–M14)" + "Production
  Readiness (P0–P2)" sections as implementing-session guard-rails (mirror Neo4jDriver
  3-tier fallback for M11, distinct from the existing `write_spec` skill, etc.).
- `PROGRESS.md` — this entry.

**Verification:** docs-only, so the bar is "nothing regressed". Full offline suite
re-run, all green:
- `verify_agent_core.py` → ALL PASS (11/11)
- `verify_m0_loop.py` → ALL PASS (3/3)
- `verify_m3_learning.py` → ALL PASS (6/6)
- `verify_m5_evolution.py` → OK (6/6)
- `verify_m6_workqueue.py` → OK (6/6)
- `verify_m7_scope_guard.py` → 2/2
- `verify_m8_repo_split.py` → 3/3
- `verify_m9_maf_pilot.py` → 6/6
- `verify_m10_assert.py` → 6/6
- `evals/ci_gate.py` → exit 0 (no regression)

**Notes / follow-ups:** Recommended next implementation is **P0.1 durable execution**
(highest-leverage, internal-only) per the production track, or **M11 Spec Registry**
per the feature track — a detailed M11 design exists from this session's planning
(spec_registry driver methods, two new skills, scope-guard extension, 4/4 verify).
Each milestone is its own feature branch → PR to `dev` (PRD §12).

---

## 2026-06-14 — M10: ASSERT / Rubric Eval Integration (M10)
**Branch:** `claude/previous-session-plan-xpz609`  ·  **Commit:** pending

**What:** Implemented M10 from `M6-M10-Upgrade-Plan.md` — the final Frontier
milestone. Replaced the bespoke 1–5 LLM "judge" with declarative, machine-checkable
**ASSERT specs**: a registry of rubrics, one or more per governance Rule (1–9) plus
the M7 scope invariant. `evals/scorers/judge.py` now reports the weighted pass rate
over the applicable *trial* specs (Rules 1, 2, 4, 8 + M7 scope) — fully deterministic
and offline. The optional Azure 1–5 rating is kept only as a secondary `llm_rating`
calibration signal; it no longer drives the score. Governance specs (Rules 3, 5, 6,
7, 9) grade the harness itself. The Evidence Stack can now annotate each layer with
the rubric verdict backing it, and the event bus gained an opt-in, offline-safe OTel
export. The `pass^k` runner and `ci_gate` are unchanged in shape — `judge.score()`
keeps its public contract. judge_avg rose 0.40 → 1.00 (the rubric is now a hard,
deterministic gate); baseline.json re-locked to 1.00.

**Files:**
- `evals/assert_specs.py` (new) — `AssertSpec` dataclass; `TRIAL_SPECS` +
  `GOVERNANCE_SPECS` registries; `evaluate_specs()`, `evaluate_governance()`,
  `evaluate_all()`, `all_rule_ids()`, `_safe_check()` (buggy spec → deterministic
  fail). Pure, offline, no LLM.
- `evals/scorers/judge.py` — rewritten to be ASSERT-driven. `score()` returns
  `{value, reason, method ("assert"|"assert+llm"), specs, heuristic_value,
  needs_human_review, llm_rating}`. Optional `_llm_rating()` enrichment only flags
  calibration disagreement. `_heuristic_score()` kept as a back-compat shim.
- `evals/evidence_stack.py` — `build_evidence_stack(state, asserts=None)`; when the
  `specs` mapping is supplied, each layer gets `assert_rule` + `assert_verdict`.
  `asserts=None` → identical pre-M10 6-layer shape (verify-m6/m7/m8 unaffected).
- `orchestrator/events.py` — opt-in OTel mirror: `otel_enabled()`, `_get_tracer()`
  (cached), `_otel_emit()` wired into `emit()`, `_reset_otel_cache()` test hook.
  No-op unless `CONTINUUM_OTEL` set AND `opentelemetry` importable; errors swallowed.
- `scripts/verify_m10_assert.py` (new) + `Makefile` `verify-m10` target.
- `evals/results/baseline.json` — judge_avg re-locked 0.40 → 1.00 (intentional;
  ci_gate exits 0, this is an improvement not a regression).
- `.gitignore` — ignore transient `evals/results/pass_k_*.json`.
- `CLAUDE.md` / `README.md` — non-negotiable rules, eval-harness + ASSERT sections,
  `CONTINUUM_OTEL` env var, two new invariants, badges, timeline, verification matrix.

**Verification:**
- `python scripts/verify_agent_core.py` → 11/11 PASS
- `python scripts/verify_m0_loop.py` → 3/3 PASS
- `python scripts/verify_m3_learning.py` → 6/6 PASS
- `python scripts/verify_m5_evolution.py` → 6/6 PASS
- `python scripts/verify_m6_workqueue.py` → 6/6 PASS
- `python scripts/verify_m7_scope_guard.py` → 2/2 PASS
- `python scripts/verify_m8_repo_split.py` → 3/3 PASS
- `python scripts/verify_m9_maf_pilot.py` → 6/6 PASS
- `python scripts/verify_m10_assert.py` → 6/6 PASS
- `python evals/ci_gate.py` → exit 0 (det/per-trial/pass^k stable; judge_avg 0.40→1.00 improve, re-baselined)
- `python evals/pass_k_runner.py --k 2 --filter crud-01` → runs clean (judge=1.00 reflects ASSERT)

**Notes / follow-ups:**
- ASSERT rubric is deterministic offline → judge_avg is a stable 1.00 for the
  golden cases (the 4 applicable trial specs all pass; M7 scope is `na` because the
  golden cases supply no business_mappings). Re-baselining to 1.00 turns the rubric
  into a real hard gate: any future run that regresses a Rule now trips ci_gate.
- The deterministic `pass` gate (weighted ≥ 0.8) is unchanged and still governs
  per-trial pass/fail — ASSERT enriches the *judge* dimension only. det_weighted,
  per_trial_rate, pass_k_rate are byte-identical to the pre-M10 baseline.
- OTel is live-only/optional (like Azure/Neo4j/ADO/MAF): the canonical offline env
  has no `opentelemetry` package, so the export stays a no-op and is never imported.
  The verify script proves the no-op holds both with the flag unset and with the flag
  set but the package absent, and that events still flow.
- **Next:** M6–M10 plan complete. Future work could wire OTel spans to a live
  collector, add ASSERT specs for new rules as they land, and surface per-layer
  `assert_verdict` in the UI Evidence tab.

---

## 2026-06-14 — M9: MAF Harness Pilot (M9)
**Branch:** `claude/previous-session-plan-xpz609`  ·  **Commit:** pending

**What:** Implemented M9 from `M6-M10-Upgrade-Plan.md`. Piloted an alternative
agent-execution path on the Microsoft Agent Framework (MAF) for one opted-in
agent (Backend), letting MAF own the tool-calling loop instead of the hand-rolled
`_run_llm` loop. The pilot is **opt-in (default off)**, gated on both the
`CONTINUUM_MAF_AGENTS` env flag AND the `agent_framework` package being importable
AND a live model being resolved. Any MAF problem degrades to the LangChain loop,
then offline — so a missing/broken MAF install can never break a run. In the
offline/thin environment the pilot is fully dormant and the deterministic path is
byte-for-byte unchanged.

**Files:**
- `orchestrator/maf_runner.py` (new) — `MAFUnavailable`, `maf_enabled_roles()`,
  `maf_package_available()` (cached), `should_use_maf(role)`, `maf_tool_specs()`,
  `run_maf_agent(spec, state, skills, ctx)`. Lazily imports `agent_framework`;
  reuses `agent_runner`'s prompt/parse/skill-invocation helpers via deferred
  imports (no import cycle).
- `orchestrator/agent_runner.py` — `run_agent()` dispatch now routes opted-in
  roles to `run_maf_agent()`, with `MAFUnavailable` → `_run_llm` fallback inside
  the existing outer try (which still falls back to offline).
- `scripts/verify_m9_maf_pilot.py` (new) + `Makefile` `verify-m9` target.
- `CLAUDE.md` — execution-model diagram + dispatch description updated; new env
  vars (`CONTINUUM_MAF_AGENTS`, `CONTINUUM_TARGET_REPO`); new MAF invariant; added
  `verify_m9_maf_pilot.py` to non-negotiable rules.

**Verification:**
- `python scripts/verify_agent_core.py` → 11/11 PASS
- `python scripts/verify_m0_loop.py` → 3/3 PASS
- `python scripts/verify_m3_learning.py` → 6/6 PASS
- `python scripts/verify_m5_evolution.py` → 6/6 PASS
- `python scripts/verify_m6_workqueue.py` → 6/6 PASS
- `python scripts/verify_m7_scope_guard.py` → 2/2 PASS
- `python scripts/verify_m8_repo_split.py` → 3/3 PASS
- `python scripts/verify_m9_maf_pilot.py` → 6/6 PASS
- `python evals/ci_gate.py` → exit 0 (no regression vs baseline)
- `cd ui && npm run build` → clean (328 kB bundle)

**Notes / follow-ups:**
- MAF (`agent-framework`) is an optional live-only dependency — not installed in
  the canonical offline env, and there are no Azure creds here, so the *live* MAF
  call cannot be exercised in this environment. This mirrors how Azure/Neo4j/ADO
  live paths are already handled: lazily imported, best-effort, always with a
  deterministic fallback. The verify script proves the parts that ARE offline-
  testable: capability gating, opt-in semantics, env parsing, `MAFUnavailable`
  on missing package, and the MAF→LangChain fallback routing (via monkeypatch).
- The MAF API surface (`AzureOpenAIChatClient.create_agent(...).run(...)`) is
  pinned to the published `agent-framework` package; every MAF call is wrapped so
  any API drift raises `MAFUnavailable` and falls back rather than crashing.
- **Next:** M10 (ASSERT / Rubric Eval Integration) — replace
  `evals/scorers/judge.py` with ASSERT specs for Rules 1–9 + the M7 scope
  invariant; keep the `pass^k` runner. See `M6-M10-Upgrade-Plan.md`.

---

## 2026-06-13 — M8: Two-Layer Repo Split / .pdlc/ (M8)
**Branch:** `claude/previous-session-plan-xpz609`  ·  **Commit:** pending

**What:** Implemented M8 from `M6-M10-Upgrade-Plan.md`. Layer 1 (Continuum
pipeline) is now architecturally separated from Layer 2 (target application).
After a pipeline run, `emit_pdlc_artifacts()` writes a `.pdlc/` directory into
the target repo containing: `run_manifest.json`, `evidence.json` (6-layer
Evidence Stack), `contracts/openapi.yaml`, `contracts/schema.sql`, and
`generated/<files>`. Wired into `_execute_pipeline` via the `CONTINUUM_TARGET_REPO`
env var (non-fatal if absent). A minimal `samples/target-app/` skeleton
demonstrates where Layer 2 lives. `pdlc_written` event surfaces in the ActivityStream.

**Files:**
- `orchestrator/pdlc.py` (new) — `emit_pdlc_artifacts(state, target_path)`;
  pure file I/O, offline-safe.
- `skills/emit_pdlc/v1.0/skill.py` (new) — agent-callable wrapper around the
  orchestrator module.
- `orchestrator/state.py` — added `pdlc_path: Optional[str] = None`.
- `api/main.py` — added `os` import, `from orchestrator.pdlc import
  emit_pdlc_artifacts`; wired M8 step after Memory in `_execute_pipeline`
  (conditional on `CONTINUUM_TARGET_REPO`); `_state_to_dict` exposes `pdlc_path`.
- `samples/target-app/` (new) — minimal FastAPI skeleton with `.pdlc/README.md`
  explaining the two-layer architecture.
- `ui/src/types.ts` — added `pdlc_written` to `EventType`.
- `ui/src/components/ActivityStream.tsx` — style + summary for `pdlc_written` event.
- `scripts/verify_m8_repo_split.py` (new) + `Makefile` `verify-m8` target.
- `CLAUDE.md` — added `verify_m8_repo_split.py` to non-negotiable rules.

**Verification:**
- `python scripts/verify_agent_core.py` → 11/11 PASS
- `python scripts/verify_m0_loop.py` → 3/3 PASS
- `python scripts/verify_m6_workqueue.py` → 6/6 PASS
- `python scripts/verify_m7_scope_guard.py` → 2/2 PASS
- `python scripts/verify_m8_repo_split.py` → 3/3 PASS
- `python evals/ci_gate.py` → exit 0 (no regression vs baseline)
- `cd ui && npm run build` → tsc + vite build clean (212 modules)

**Notes / follow-ups:**
- `emit_pdlc_artifacts` is non-fatal in the pipeline — if `CONTINUUM_TARGET_REPO`
  is unset (standard offline mode), no `.pdlc/` is written and the run completes
  normally. All verify suites still pass without the env var set.
- The `samples/target-app/.pdlc/` directory is tracked in git (contains only a
  README). After a real run, the operator decides whether to commit the run
  artifacts (for audit trail) or add `.pdlc/` to the target app's `.gitignore`.
- `pdlc_written` event carries `{pdlc_path, files_written}` so the UI (Activity
  tab) can confirm Layer 2 write.
- **Next:** M9 (MAF Harness Pilot) — wrap the Backend agent on Microsoft Agent
  Framework on a branch. See `M6-M10-Upgrade-Plan.md`.

---

## 2026-06-13 — M7: Mapping Fidelity / Scope-Guard (M7)
**Branch:** `claude/previous-session-plan-xpz609`  ·  **Commit:** pending

**What:** Implemented M7 from `M6-M10-Upgrade-Plan.md`. The operator can now
supply `business_mappings` (e.g. `[{code:"BR",label:"Branch"}]`) when submitting
a feature request. After the developer chain runs, `gate_scope_conformance` scans
the generated code for those exact codes; a mismatch (missing or extra) escalates
immediately to `run_blocked` (no auto-retry). The Evidence Stack layer 4 is wired
to real `state.mapping_fidelity.exact_match` (was a stub in M6). The UI gains a
collapsible business-mappings form in the WorkQueue and a `MappingFidelity` panel
in the Evidence tab.

**Files:**
- `orchestrator/state.py` — added `business_mappings: List[Dict]` and
  `mapping_fidelity: Optional[Dict]`.
- `skills/scope_guard/v1.0/skill.py` (new) — `async def scope_guard(...)`,
  pure offline scan; returns `{supplied, found, extra_in_code, missing_in_code,
  exact_match}`.
- `orchestrator/gates.py` — added `_check_scope()` helper and
  `gate_scope_conformance(state)` (side-effect: sets `state.mapping_fidelity`).
- `api/main.py` — `FeatureRequest` extended with `business_mappings`; imported
  `gate_scope_conformance, update_gate_status`; wired scope gate at step 2.5
  in `_execute_pipeline` (after developer, before security); `start_run()` passes
  `business_mappings` to state; `_state_to_dict` exposes both new fields;
  `_run_summary` exposes `reject_reason`.
- `evals/evidence_stack.py` — layer 4 now driven by `_scope_status()` /
  `_scope_detail()` (reads real `state.mapping_fidelity`; skips gracefully when
  no business_mappings supplied).
- `ui/src/types.ts` — added `BusinessMapping`, `MappingFidelity` interfaces;
  extended `RunSummary` with M7 fields.
- `ui/src/lib/api.ts` — `startRun()` accepts optional `businessMappings` param.
- `ui/src/components/WorkQueue.tsx` — collapsible business-mappings form (code +
  label pairs, D11 shield note); passes mappings to `startRun`.
- `ui/src/components/MappingFidelity.tsx` (new) — two-column supplied-vs-found
  table, exact-match pill, extra/missing warnings.
- `ui/src/App.tsx` — `MappingFidelity` wired into the Evidence tab.
- `scripts/verify_m7_scope_guard.py` (new) + `Makefile` `verify-m7` target.
- `CLAUDE.md` — added `verify_m7_scope_guard.py` to non-negotiable rules.

**Verification:**
- `python scripts/verify_agent_core.py` → 11/11 PASS
- `python scripts/verify_m0_loop.py` → 3/3 PASS
- `python scripts/verify_m3_learning.py` → 6/6 PASS
- `python scripts/verify_m5_evolution.py` → 6/6 PASS
- `python scripts/verify_m6_workqueue.py` → 6/6 PASS
- `python scripts/verify_m7_scope_guard.py` → 2/2 PASS
- `python evals/ci_gate.py` → exit 0 (no regression vs baseline)
- `cd ui && npm run build` → tsc + vite build clean (211 modules)

**Notes / follow-ups:**
- Scope gate runs **only** when `business_mappings` is non-empty AND `local_verify`
  is green (or absent in offline path). Empty mappings → gate always skipped (pass).
- No auto-retry on scope mismatch — the run parks in `blocked` state so the
  operator reviews the MappingFidelity panel and either adjusts the code or
  the supplied mappings before resubmitting.
- The `_SCOPE_KEY_RE` regex catches string literals used as dict keys/enum values
  (`"BR":`, `'ACC':`, etc.). It may flag HTTP method literals (`"GET"`, `"POST"`)
  in unusual dict contexts; for production hardening, restrict the regex or add a
  allow-list. For M7 offline test cases this is not an issue.
- `_run_summary()` now exposes `reject_reason` (fixes gap where Returned panel
  showed no reason in M6).
- **Next:** M8 (Two-Layer Repo Split) — see `M6-M10-Upgrade-Plan.md`.

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
