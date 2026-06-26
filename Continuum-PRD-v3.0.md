# Continuum — SDLC Control Tower
## Product Requirements Document v3.0

| | |
|---|---|
| **Version** | 3.0 |
| **Status** | Active — M0–M10 baseline delivered / M11–M14 roadmap |
| **Supersedes** | PRD v2.2 (M0–M5 POC scope) |
| **Date** | June 2026 |
| **Author role** | Senior Solution Architect |
| **One-liner** | A plain-English feature request enters as an artifact and moves through a 15-state, policy-governed SDLC lifecycle — agents execute, deterministic gates verify, humans approve at four named control points, and the pipeline learns from every run. |
| **Core stack** | LangGraph 1.x · Azure AI Foundry (Canada) · Neo4j 5.15 · PostgreSQL (LangGraph checkpointer) · FastAPI · React 18 + SSE · Azure DevOps MCP |

---

## 0. Changelog — v2.2 → v3.0

v2.2 was a POC spec for M0–M5: one pipeline, one app, walking-skeleton validation. v3.0 reflects what was actually built (M0–M10), what was learned from a parallel enterprise engagement (the "AI-Powered PDLC Pipeline" sibling project), and the forward roadmap (M11–M14) informed by the February 2026 Spec-Driven Development literature (arXiv 2602.00180) and Microsoft BUILD 2026 / MAF 1.0 GA.

**What changed:**
- Scope expanded from POC to platform: artifact-centric state machine, multi-run Work Queue, Mapping Fidelity gate, Evidence Stack
- Architecture reframed: *artifacts* are central, not agents — policies decide transitions, agents produce artifacts, humans approve
- Forward roadmap: Spec Registry (M11), Compliance Report (M12), 15-State Machine (M13), MAF Harness Graduation (M14)
- Spine/Frontier framing adopted throughout: stable vs. provisional, explicit graduation criteria per milestone
- SDD alignment: Continuum is now formally characterised as a Spec-Anchored SDD platform — specs execute as validation gates, not documentation

---

## 1. Executive Summary

Continuum is an AI-native engineering operating system. It takes a plain-English intent and carries it through the entire software delivery lifecycle — business analysis, architecture, implementation, security, review, test, deployment, verification — with every stage gated, every artifact persisted, every human decision recorded.

The platform is built on three validated architectural principles:

**The harness is the product, not the LLM.** Models are tiered, swappable configuration. The orchestration graph, the deterministic gates, the policy engine, and the Neo4j Engineering Context Graph are what get optimised. This is the IP.

**Artifacts are central.** A Story, ADR, PR, or Incident is the primary entity. It moves through states. Agents produce artifacts. Policies decide whether an artifact can advance. Humans approve at four named gates. This is the insight the Control Tower whiteboard encodes — and it is a cleaner model than "the orchestrator routes between agents."

**Termination by verification, not model confidence.** Every stage ends when deterministic sensors say it's done — ruff, mypy, pytest, bandit, Playwright — not when the agent says "I'm finished." This is the core of the harness, and it is what makes the system trustworthy enough to touch production-adjacent work.

The platform's relationship to Spec-Driven Development: Continuum independently arrived at SDD's central insight — specifications execute as validation gates, not documentation — and operationalises all three of the arXiv paper's levels (spec-first at Gate 1, spec-anchored via the Scope-Guard, spec-as-source via the Spec Registry in M11).

---

## 2. Problem Statement

Software delivery in enterprise environments suffers from three structural failure modes that no current AI coding tool addresses:

**Context loss at handoffs.** BSA → architect → developer → reviewer → QA → security — each transition drops context. AI autocomplete tools (Copilot, Cursor) operate within one role's slice. They don't own the thread from intent to merge.

**"AI graded its own homework."** Current agentic systems generate code and evaluate it using the same model. There is no independent ground truth. This produces confident hallucinations in production code. The 2025 Faros AI research (10,000 developers, 1,255 teams) found AI adoption increases throughput but moves the bottleneck downstream to verification. The bottleneck is now "was the AI right?" — and AI answering its own question is not verification.

**No institutional memory.** Each run starts cold. The same mistakes are made in run 100 as in run 1. No system connects what went wrong in the last feature to how the next one should be built.

Continuum addresses all three. The pipeline owns the full thread (intent → merge). Deterministic sensors are the verification layer, external to the model. Neo4j episodic memory makes run N+1 better than run N.

---

## 3. Goals & Non-Goals

### Goals (M0–M10 — delivered)
- Intent → merge-ready PR in Azure DevOps, fully orchestrated, gated, and audited
- Multi-run Work Queue: a human reviewer sees all in-flight runs, what needs their attention, and the state of each
- Mapping Fidelity gate (D11): AI-generated code is verified to use exactly the business mappings a human supplied — nothing invented
- Evidence Stack: "done" is six independently-sourced signals, never a single model confidence score
- Episodic memory: Neo4j writes one Episode node per run; run N+1 retrieves and grounds on past decisions (verified: score=1.0 retrieval)
- Eval harness: pass^k reliability measurement, 20 golden cases, CI regression gate (2pp regression → exit 1, proven)
- Evolution Agent: observe → diagnose → propose harness improvements, human_promote() as the only mutation path
- Live React UI: React Flow agent graph + SSE event stream + Gate Inbox + Artifact Viewer
- Zero-credential offline path: all verification suites pass without Azure/Neo4j/ADO credentials

### Goals (M11–M14 — roadmap)
- Persistent Spec Registry: specs survive beyond a single run, versioned, queryable, used to detect drift
- Compliance Report: structured artifact packaging spec + gate decisions + evidence + audit trail (EU AI Act / OSFI alignment)
- 15-State SDLC State Machine: full lifecycle from IDEA/INTAKE to CLOSED, as a first-class policy-governed graph
- MAF Harness Graduation: Microsoft Agent Framework 1.0 harness primitives adopted for context compaction, plan/execute, skill providers

### Non-Goals
- Autonomous production deploy without human sign-off (humans approve every promotion; agents prepare, not approve)
- Replacing human architects or senior engineers (the platform augments them — Gate 1 and Gate 2 exist precisely because the human's judgment is the quality signal)
- Model fine-tuning
- Real-time multi-user concurrent editing of the same artifact

---

## 4. Architecture — Current State (M0–M10)

### 4.1 The 8-Layer Platform Stack

```
┌─────────────────────────────────────────────────────────┐
│  1. EXPERIENCE PLANE                                     │
│  Work Queue · Spine · Gate Inbox · Artifact Viewer       │
│  Activity Stream · Agent Graph (engineer view)           │
├─────────────────────────────────────────────────────────┤
│  2. CONTROL PLANE  (The Brain)                           │
│  LangGraph Supervisor · Policy Engine · Approval Engine  │
│  State Machine · Context Builder · OTel Telemetry        │
├─────────────────────────────────────────────────────────┤
│  3. ENGINEERING CONTEXT GRAPH  (The Moat)                │
│  Neo4j · Artifacts as primary entities                   │
│  Typed relationships: IMPLEMENTS · DEPENDS_ON · TESTS    │
│  DEPLOYS · CAUSES · APPROVES · GENERATES · USES · OWNS   │
├─────────────────────────────────────────────────────────┤
│  4. AGENT WORKFORCE  (Intelligence Layer)                │
│  BSA · Architect · Planner · DB · Backend · Frontend     │
│  Security · Code Review · Test · Memory · Evolution      │
├─────────────────────────────────────────────────────────┤
│  5. EXECUTION TIER  (Sandbox)                            │
│  Azure Container Apps dynamic sessions · Hyper-V         │
│  rm -rf / destroys only its own guest                    │
├─────────────────────────────────────────────────────────┤
│  6. TOOL & MCP FABRIC                                    │
│  Azure DevOps · GitHub · Snyk · SonarQube · Datadog      │
├─────────────────────────────────────────────────────────┤
│  7. KNOWLEDGE & MEMORY FABRIC                            │
│  Neo4j Episodes · Vector RAG · ASSERT evals              │
│  pass^k runner · Golden dataset (20 cases)               │
├─────────────────────────────────────────────────────────┤
│  8. GOVERNANCE & CONTROLS                                │
│  Data Privacy · Policy Enforcement · human_promote()     │
│  Audit trail (append-only Postgres)                      │
└─────────────────────────────────────────────────────────┘
```

### 4.2 The Pipeline (current, 9 stages)

```
Intent → [1] Spec Agent → Gate 1 (human: plan)
       → [2] Implementation Agent → Gate 2 (human: diff + preview)
       → Deploy SIT → [3] Verification Agent → Automated Quality Gate
       → Promote UAT
```

Every stage reads and writes the **Work-Item State** (PostgreSQL durable checkpointer). Every gate decision is recorded append-only. The orchestrator has zero work tools — it only routes and finishes.

### 4.3 The 3-Tier Permission Model

| Tier | Who | What | Enforced by |
|---|---|---|---|
| Read-Only | All agents | Query Neo4j, read repo, search context | Default |
| Sandbox-Edit | Dev/Test agents | Patch files, run builds — inside microVM only | Hyper-V boundary |
| Full-Access | Named host skills | ADO writes, PR creation, deploy | `interrupt()` human gate |

### 4.4 Deterministic Gate Sensors (in order, cheapest first)

1. `gate_local_verify` — ruff + mypy + pytest + py_compile (one combined gate)
2. `gate_sast` — bandit + semgrep (security scan)
3. `gate_contract_validate` — OpenAPI schema conformance
4. `gate_scope_conformance` (M7) — mapping fidelity: supplied vs. found in generated code

**Max 3 retries per gate.** Retry exhaustion → `run_blocked`, human escalation. Gate rejection → `run_returned`, loop back to BSA/Architect with recorded reason.

### 4.5 Evidence Stack ("done" = 6 independent signals)

| Layer | Signal | Source | Independence |
|---|---|---|---|
| 1 | Build / compile | ruff + mypy | Objective |
| 2 | Regression suite | pytest | Independent ground truth |
| 3 | Acceptance-criteria check | gate_contract_validate | End-to-end |
| 4 | Scope conformance | gate_scope_conformance | Diff vs. spec |
| 5 | Lint + secret scan | gate_sast | Static |
| 6 | Human review | Gate 1 + Gate 2 | Final judgment |

### 4.6 The Learning Loop

```
Run N:   Spec → ... → Memory Agent writes Episode to Neo4j
Run N+1: BSA calls graphrag_query() FIRST
         → retrieves Episode from run N (score=1.0 proven)
         → grounds new spec on past decisions
         → fewer mistakes, lower retry counts
```

### 4.7 The Evolution Agent

```
observe() → diagnose() → propose() → evaluate() → human_promote()
                                                         ↑
                              THE ONLY CODE PATH THAT TOUCHES THE HARNESS
```

Zero auto-apply paths. Agent Optimizer (Microsoft) recommended as candidate-generation engine in M14; `human_promote()` stays the governance gate regardless.

### 4.8 Run Lifecycle States (M6)

`running` → `waiting_gate` → `running` (approved) / `returned` (rejected)
`running` → `blocked` (retry exhaustion, human escalation)
`running` → `done` (quality gate green, promoted to UAT)
`running` → `failed` (unrecoverable error)

---

## 5. Spec-Driven Development Alignment

Continuum maps directly to the three-level SDD spectrum (arXiv 2602.00180):

| SDD Level | Definition | Continuum implementation |
|---|---|---|
| Spec-First | Spec written before code, guides implementation | Gate 1: human approves spec before any code runs |
| Spec-Anchored | Spec maintained in sync with code, used for validation | Gate 2: diff verified against Gate 1 spec; scope-guard proves conformance |
| Spec-as-Source | Spec is the authoritative source; code derives from it | M11 Spec Registry: persistent, versioned, drift-detecting |

The key distinction the paper formalises, and which Continuum operationalises: **traditional specs are read by humans; SDD specs execute as validation gates.** Every acceptance criterion produced by the Spec Agent becomes a concrete Playwright assertion in SIT. The spec is the test.

---

## 6. The 9 Rules (enforced in code, not a wiki)

| # | Rule | Enforcement |
|---|---|---|
| 1 | Never push and pray | Local CI mirror runs before any push (`harness/ci-mirror.Dockerfile`) |
| 2 | Every stage is a gate | Conditional edges in LangGraph — no skip paths exist (`orchestrator/graph.py`) |
| 3 | Verify locally what CI verifies remotely | Identical Docker image locally and in Azure Pipelines |
| 4 | Bound the auto-fix loop | Max 3 retries on `GateStatus.retry_count` → human escalation |
| 5 | Skills are atoms, not prompts | Versioned functions: `raise_pr@1.0`, `run_sast@1.0` (`skills/{name}/v1.0/skill.py`) |
| 6 | Production deploys are retags | Same image from SIT to UAT — never rebuilt between environments |
| 7 | The pipeline is the product | Harness versioned in git; models are swappable config |
| 8 | Plans are contracts | Every BSA/Architect/Planner output is a machine-checked PEV contract (`harness/pev.py`) |
| 9 | The harness is governed | `human_promote()` is the only mutation path (`evolution/promoter.py`) |

---

## 7. Milestone Baseline (M0–M10)

| Milestone | What shipped | Key verification |
|---|---|---|
| M0 | Walking skeleton: agent core, gates, Neo4j DAG, 11/11 + 3/3 offline | 11/11 + 3/3 |
| M1 | Live LLM (AzureChatCompletions), real skills, ADO integration, human `interrupt()` gates | 11/11 + 3/3 |
| M2 | React Flow live graph, SSE event stream, Gate Inbox, Artifact Viewer | 11/11 + 3/3 |
| M3 | Neo4j episodic memory, GraphRAG, Memory Agent, score=1.0 retrieval | +6/6 learning |
| M4 | pass^k runner, 20 golden cases, CI gate (2pp regression → exit 1) | +CI gate |
| M5 | Evolution Agent, bounded decomposition, `human_promote()` | +6/6 evolution |
| M6 | Work Queue UI, Evidence Stack, run metrics, blocked/returned states | +verify-m6 |
| M7 | Mapping Fidelity (D11), scope-guard gate, MappingFidelity UI component | +verify-m7 |
| M8 | Two-Layer Repo Split: Layer 1 pipeline platform / Layer 2 `.pdlc/` per-app | +two-layer |
| M9 | MAF Harness Pilot: Backend agent wrapped on MAF 1.0 harness primitives | +maf-pilot |
| M10 | ASSERT + Rubric eval integration, OTel emission, pass^k over ASSERT suite | +assert |

**All M0–M10 verification suites pass with zero external credentials.**

---

## 8. Forward Roadmap (M11–M14)

### 8.1 Spine vs. Frontier framing

STABLE SPINE (M11–M12): build in this order, each independently shippable.
FRONTIER (M13–M14): real, sequenced, not started until M11–M12 are proven.

---

### M11 — Spec Registry (Spec-as-Source foundation)

**Problem it solves:** today a spec lives in one run's work-item state and dies with it. There is no persistent record of "the current agreed spec for the Product Selection page." This means every new run starts from intent alone, with no knowledge of past design decisions. Architectural drift is undetected.

**What it is:** a persistent, versioned, queryable spec store backed by Neo4j. A `Spec` node per feature/component, linked to `Episode` nodes (M3), `Artifact` nodes, and the `Run` that produced it. Before any new run, the BSA agent queries the Registry ("what's the existing spec for this area?") and must either conform to it or explicitly supersede it — which requires Gate 1 sign-off with a `supersedes` reason recorded on the new Spec node.

**Key design decisions:**
- Spec versioning is immutable append-only (like the audit trail): a new Spec creates a new node linked to the previous version via `SUPERSEDES` relationship. No in-place edits.
- The scope-guard gate (M7) gains a second check: not just "does the code match the business mappings from this run's Gate 1?" but "does the spec from this run conform to or explicitly supersede the existing Registry spec for this component?"
- The BSA agent's system prompt is extended: it must read the Registry before drafting. Past specs are grounding, same as past episodes.
- A new `GET /specs/{component}` endpoint returns the current spec tree for a component, including version history and supersession chain.

**New files:**
- `graph_db/spec_registry.py` — `write_spec()`, `get_current_spec()`, `get_spec_history()`, `mark_superseded()`
- `skills/query_spec_registry/v1.0/skill.py` — BSA-facing retrieval skill
- `skills/write_spec_registry/v1.0/skill.py` — post-Gate-1-approval write skill
- `api/main.py` — `GET /specs/{component}`, `GET /specs` (list)
- `ui/src/components/SpecRegistry.tsx` — read-only spec history browser in the UI

**Acceptance criteria:**
- Run 1 on "Product Selection page" creates a `Spec` node in Neo4j
- Run 2 on the same component: BSA retrieves Run 1's spec before drafting
- Run 2's spec that supersedes Run 1 records the `SUPERSEDES` relationship and the `supersedes_reason`
- Run 2's scope-guard gate checks conformance to the Registry spec, not just the in-run Gate 1 contract
- `GET /specs/product-selection` returns the version chain
- `scripts/verify_m11_spec_registry.py` → 4/4 PASS
- All M0–M10 suites still pass

**Why this is M11 and not earlier:** M7's scope-guard is a prerequisite — M11 extends the same conformance logic from "matches this run's Gate 1 mappings" to "matches the Registry's current spec for this component." Without M7, there's nothing to extend.

---

### M12 — Compliance Report

**Problem it solves:** for a financial services customer (OSFI, SOX) or any EU AI Act high-risk deployment, "the pipeline ran and the tests passed" is not evidence. Evidence is a structured, tamper-evident artifact that packages spec, gate decisions, evidence stack, human approvals, and audit trail into something an auditor can read. Currently that information exists across Postgres, Neo4j, and the event bus — but there is no endpoint that assembles it.

**What it is:** a `GET /runs/{run_id}/compliance-report` endpoint that produces a structured JSON (and optionally a rendered PDF or HTML) compliance artifact for a completed run. The artifact packages:

1. **Run metadata** — run ID, intent, target component, timestamps, model cost, lead time
2. **Spec** — the Gate 1 approved spec (from Spec Registry if M11 is present, else from work-item state)
3. **Business mappings** — human-supplied at intent time (D11), with the scope-guard's conformance result
4. **Gate decisions** — Gate 1 and Gate 2: who approved, at what time, what they reviewed (spec text / diff / preview)
5. **Evidence Stack** — all 6 layers, each with status and detail
6. **Audit trail** — full append-only log from Postgres, in chronological order
7. **Model and harness versions** — which model tier was used, which harness version (`git describe`), which skills
8. **Compliance assertions** — boolean checklist: scope-guard passed, no auto-apply paths taken, human_promote() not called without a prior evaluate() passing, all gates green

**Why this matters for M12 specifically (not later):** the EU AI Act high-risk obligations deadline is August 2, 2026. Financial services customers need this artifact before they can put Continuum in front of production-adjacent work. The information to produce it already exists in M0–M11. This is packaging, not new capability.

**New files:**
- `api/compliance.py` — `build_compliance_report(run_id, state) -> dict`
- `api/main.py` — `GET /runs/{run_id}/compliance-report` (JSON) + `GET /runs/{run_id}/compliance-report.html`
- `ui/src/components/ComplianceReport.tsx` — rendered report in the UI, with a download button

**Acceptance criteria:**
- `GET /runs/{run_id}/compliance-report` returns a valid JSON for any completed run
- All 8 sections present; any missing data is explicit `null` with a reason, not silently omitted
- `scripts/verify_m12_compliance.py` → 3/3 PASS (complete run, blocked run, returned run each produce a valid report)
- All M0–M11 suites still pass

---

### M13 — 15-State SDLC State Machine (Frontier)

**Graduation criteria for starting:** M11 + M12 landed and proven. The two-layer repo split (M8) is in.

**Problem it solves:** Continuum's current pipeline has 9 stages, hard-coded in `_execute_pipeline()`. The Control Tower architecture (from the session's whiteboard) defines 15 states with explicit entry/exit criteria, allowed transitions, required artifacts, required approvals, and SLAs per state. The current system has none of this formalism — states are implicit, transition rules are buried in `_route()`, and exception/return paths are ad hoc. This makes it hard to onboard a new app, hard to explain to a stakeholder, and impossible to let a policy engine govern transitions.

**What it is:**

The 15 states, as a first-class `SDLCStateMachine` in code:
```
NEW → EPIC_APPROVED → STORIES_READY → ARCH_READY → IMPL_READY
    → IN_PROGRESS → CODE_COMPLETE → TESTING → TESTS_PASSED
    → SECURITY_REVIEW → SECURITY_APPROVED → RELEASE_READY
    → DEPLOYED → IN_PRODUCTION → CLOSED
```

Each state has a `StateDefinition`: entry criteria (what must be true to enter), exit criteria (what must be true to leave), required artifacts (which Neo4j nodes must exist), required approvals (which human gates), quality gates (which sensor functions), allowed transitions (forward and return), and SLA targets.

The **Policy Engine** (`orchestrator/policy_engine.py`) is a deterministic function `can_transition(artifact, from_state, to_state) -> (bool, reason)`. Policies, not agents, decide state transitions. Agents produce artifacts and update state fields; the policy engine gates every transition.

The **4 HITL gates** become named, typed objects:
- G1 — Business Approval (after EPIC_APPROVED)
- G2 — Architecture Approval (after ARCH_READY)
- G3 — Release Approval (after RELEASE_READY)
- G4 — Critical Incident Approval (post-IN_PRODUCTION, for major incidents/RCA)

**Key design decisions:**
- The state machine is an explicit `orchestrator/state_machine.py` — a Python dataclass hierarchy, not implicit in routing logic
- All existing `_route()` logic is refactored to be policy-engine calls: `if not policy_engine.can_transition(...)` → emit `run_blocked`, surface in UI
- The Neo4j `Artifact` node gains a `lifecycle_state: SDLCState` field — the artifact's state is the system of record, not the orchestrator's routing history
- Exception/return transitions are first-class: `TESTING → CODE_COMPLETE` ("tests failed"), `SECURITY_REVIEW → IN_PROGRESS` ("security issues found"), etc. are defined in `allowed_transitions` and surfaced explicitly in the UI

**Acceptance criteria:**
- A run's artifact transitions through all 15 states (offline scaffold path)
- `policy_engine.can_transition()` returns `False` with a clear reason for an invalid transition (e.g., trying to go to TESTING without CODE_COMPLETE artifacts)
- G1–G4 are named `interrupt()` points on the correct state transitions
- `scripts/verify_m13_state_machine.py` → 6/6 PASS
- All M0–M12 suites still pass

---

### M14 — MAF Harness Graduation (Frontier)

**Graduation criteria for starting:** M13 landed. M9 (MAF pilot) proven stable on Backend agent.

**Problem it solves:** M9 wrapped one agent on MAF's harness primitives as a pilot. If the pilot proves MAF is stable and pleasant to work with, M14 graduates it: all agents adopt MAF's `AgentModeProvider` (plan/execute), `AgentSkillsProvider` (maps to `skills/{name}/v1.0/`), `BackgroundAgentsProvider` (parallel DB→Backend→Frontend fan-out), and `ToolApprovalAgent` (tool-level HITL for Tier-3 operations). Hyperlight CodeAct replaces the Azure Container Apps sandbox tier (52% faster, 64% fewer tokens — measured in M9 pilot).

**What stays ours regardless of MAF adoption:**
- LangGraph orchestration graph (Foundry eval/tracing now supports LangGraph via OTel — no migration needed)
- Neo4j Engineering Context Graph
- The 9 Rules encoded as policy-engine transitions
- The 15-state machine (M13)
- `human_promote()` as the only harness mutation path

**Acceptance criteria:**
- All 11 agents run on MAF harness primitives
- Hyperlight CodeAct sandbox replaces ACA dynamic sessions
- `Agent Optimizer` (Microsoft) used as candidate-generation engine for Evolution Agent proposals; `human_promote()` stays the governance gate
- All M0–M13 suites still pass
- Measured: latency and token cost per run vs. M9 baseline

---

## 9. Open Questions & Risks

| # | Question | Stakes | Status |
|---|---|---|---|
| OQ-1 | Canada residency: does the clause mean data-at-rest in Canada or strict in-region inference? | Gates model catalog, capability ceiling, autonomy reachability — everything downstream of D29 in the sibling doc | Open — resolve before M13 implementation begins |
| OQ-2 | Shared work-item state concurrency: is the intent "one state object per work-item" or "one global object"? | Becomes a contention problem with parallel work-items if the wrong assumption is built | Resolved in M6 (`_RUNS: Dict[str, ContinuumState]` per-run) |
| OQ-3 | Evidence Stack independence: layers 1+2 (build/regression) are currently the same `gate_local_verify` gate | The "6 independent signals" claim is partially true — layers 1+2 are one signal with two sub-labels | Accepted in M6; resolved when `gate_local_verify` is split into separate lint/type/test gates (a future milestone) |
| OQ-4 | Gate-removal ladder: when and how do we automate Gate 2 (diff review) for low-risk change classes? | Autonomy is earned and must be revocable. Requires: metrics baseline + client sign-off + named accountable owner | Defined in sibling doc §7; not started — Gate 2 stays human through M14 |
| OQ-5 | ASSERT/Rubric (M10) judge calibration vs. human gold set | LLM judges hit 80–90% human agreement per literature; calibration gap matters for compliance claims | Open — needs a human labelling pass on 20 golden cases post-M10 |

---

## 10. Framework Decision Record

This is the current state of the MAF vs. LangGraph decision, grounded in the M9 pilot and BUILD 2026 announcements.

| Layer | Decision | Rationale |
|---|---|---|
| Orchestration graph | **Keep LangGraph** | Foundry eval/tracing supports LangGraph via OTel. `interrupt()` human gates and durable Postgres checkpoints are proven. No migration needed. |
| Agent harness primitives | **Adopt MAF 1.0** (M9→M14) | Context compaction, `AgentModeProvider`, `AgentSkillsProvider`, `BackgroundAgentsProvider` replace hand-rolled equivalents. Our `skills/` layout already matches `AgentSkillsProvider`'s file-discovery model. |
| Sandbox execution | **Pilot Hyperlight CodeAct** (M9), **graduate** (M14) | 52%/64% faster/cheaper than ACA sessions per published benchmark. Alpha in M9; GA timing TBC. |
| Eval ecosystem | **Adopt ASSERT + Rubric** (M10), **keep pass^k runner** | ASSERT (MIT, framework-agnostic) generates test suites from behavior specs. Rubric adds weighted scoring dims. pass^k runner wraps both as the reliability framing — ASSERT scores a trial, pass^k tells you how many of k trials all pass. |
| Self-improvement | **Evaluate Agent Optimizer** (M14) | Microsoft's Optimizer consumes traces + evals, generates ranked improvements with lineage + rollback. Our `human_promote()` stays the governance gate on top — this is the differentiated combination. |
| Memory | **Keep Neo4j** | MAF's memory is file-based. Our graph with typed relationships (IMPLEMENTS, DEPENDS_ON, CAUSES…) is richer and is the Engineering Context Graph moat. No MAF equivalent. |

---

## 11. Verification Matrix

All must pass before any milestone is declared done. Zero external credentials required for the offline suite.

```bash
make verify-offline      # 11/11 + 3/3 — agent core + loop termination
make verify-m3           # 6/6   — episodic memory, score=1.0 retrieval
make verify-m4           # CI gate — regression blocking proven
make verify-m5           # 6/6   — evolution agent, governed harness mutation
make verify-m6           # work queue, states, evidence stack, cost tracking
make verify-m7           # 2/2   — scope-guard: exact match + mismatch cases
make verify-m11          # 4/4   — spec registry (M11, when landed)
make verify-m12          # 3/3   — compliance report (M12, when landed)
make verify-m13          # 6/6   — state machine (M13, when landed)
```

---

## 12. Dispatch Guide — Claude Code Handoff Sequence

For anyone executing this plan:

```
M11 Spec Registry    → feature/m11-spec-registry → PR → dev
M12 Compliance       → feature/m12-compliance → PR → dev
M13 State Machine    → feature/m13-state-machine → PR → dev  [after M11+M12]
M14 MAF Graduation   → feature/m14-maf-graduation → PR → dev [after M13]
```

Each milestone:
1. Reads this PRD + the corrected M6–M10 plan doc (for conventions: state field names, event dict shapes, verify script pattern, offline-safe convention)
2. Implements on a feature branch
3. Runs the full verification matrix before opening a PR
4. PRs only to `dev`; `dev → main` tagged after milestone set is complete

Convention reminders (from the reconciliation pass):
- Events are plain dicts `{"event_type": str, "agent": ..., "run_id": ..., "data": ..., "timestamp": ...}` — no enum to edit
- `started_at` / `completed_at` are `Optional[float]` (UNIX timestamps) — do not add `datetime` variants
- UI uses Unicode glyphs (✓ ✗ ↺ ⏸ ▲ ↩ ○), no icon library
- `api/main.py` drives runs via `_execute_pipeline()` + `_RUNS: Dict[str, ContinuumState]` — `graph.py`'s `ContinuumGraph` is not the live path
- All new fields/events/skills must be fully offline-safe — no Azure/Neo4j/Postgres required for the verify suite

---

## 13. Glossary

| Term | Definition |
|---|---|
| **Harness** | The software layer surrounding the LLMs: permission tiers, PEV loop, deterministic gates, governed evolution. The pipeline is the product, not the model. |
| **Artifact-Centric Model** | Artifacts (Story, ADR, PR, Incident) are the primary entities. They move through states. Policies decide transitions. Agents produce artifacts. |
| **Policy Engine** | `orchestrator/policy_engine.py`. Deterministic function: `can_transition(artifact, from_state, to_state) -> (bool, reason)`. Policies, not agents, decide state transitions. |
| **Evidence Stack** | Six independently-sourced signals that constitute "done" — not a single model confidence score. |
| **Mapping Fidelity (D11)** | The scope-guard gate: AI-generated code is verified to use exactly the business mappings a human supplied at intent time. Nothing invented. Mismatch → `run_blocked`, not auto-retry. |
| **Spec Registry (M11)** | Persistent, versioned Neo4j store of specs per component. Run N+1 must conform to or explicitly supersede the Registry spec for the same component. |
| **human_promote()** | The only code path that touches the harness (Rule 9). Used by the Evolution Agent. Any proposal that would regress a gate metric is auto-rejected. |
| **pass^k** | Reliability metric: all k trials must pass. Contrasts with pass@k (at least one of k). A 70%-per-trial pipeline gets pass@3=97% but pass^3=34%. The honest number is pass^k. |
| **SDD** | Spec-Driven Development (arXiv 2602.00180). Specs execute as validation gates, not documentation. Continuum is a Spec-Anchored SDD platform today; M11 makes it Spec-as-Source. |
| **G1–G4** | The four named human approval gates: G1 Business (after epic), G2 Architecture (after design), G3 Release (before deploy), G4 Critical Incident (post-production RCA). |

---

*Continuum PRD v3.0 — June 2026*
*github.com/KIRTIRAJ4327/continuum · dev branch*
