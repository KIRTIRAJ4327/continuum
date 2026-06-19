# pdic-pipeline — Improvement Plan (over the existing walking skeleton)

**Version:** Improvement Plan v1.0 · June 2026
**Premise:** You already have a working *walking skeleton* (FastAPI + LangGraph + React/Vite cockpit, SQLite persistence, stubbed agents, SSE, audit, connector seams). The "Clean Slate v1.0" Master Guide is a good **target architecture**, but rebuilding from zero throws away working orchestration, the gate/resume model, the SSE cockpit, audit persistence, and your test suite. **This document reframes that guide as an incremental, strangler-fig upgrade of the code you already have** — sequenced so the app stays runnable and demoable after every step.

> How to read this: each workstream is an **AI-executable task card** — it names the files an assistant (Cursor / Copilot / Claude) should open, what it will *probably* find in the current skeleton, the change to make, the traps, and a crisp acceptance test. Paste one card at a time into your coding AI (that matches the Master Guide's own "paste the relevant section" workflow).

---

## 1. Verdict on the Master Build & Prompt Guide

**Keep — it gets the load-bearing things right:**

- **Build order is correct.** WorkItem model → graph + gates → checkpointer → first agent + SSE → cockpit. Foundation-first, UI-last. Don't change this sequence.
- **The three non-negotiables are the right ones:** gates are sacred, everything orbits a single well-designed `WorkItem`, all agent outputs are structured (Pydantic).
- **Postgres checkpointer from day one** is the single highest-leverage upgrade to the skeleton (the doc admits it's SQLite today). Durability + resumability is what turns a demo into a system.
- **The 2026 stack is real and current** (I verified — see §2). Tailwind v4 / shadcn / React 19 / React Flow / TanStack Query are all shipping and mutually compatible today.

**Change / add — where the guide is thin, dated, or quietly dangerous:**

| # | Issue with the guide as written | Fix in this plan |
|---|---|---|
| 1 | **It's a clean-slate rebuild.** You want to *improvise the existing solution*. A rebuild discards working code and re-introduces solved bugs. | Strangler-fig migration (§3). Every workstream upgrades existing files; the app runs after each. |
| 2 | **The `interrupt()` idempotency trap is unmentioned.** On resume, LangGraph **re-runs the whole node from the top**. "Put `interrupt()` in every gate" + a node that also writes an artifact / opens a PR / calls an LLM = duplicate side-effects on every approval. | §4.1 — isolate side-effects in their own idempotent nodes; gate nodes do *nothing* but `interrupt()`. This is the #1 correctness bug an AI will introduce if it follows the guide literally. |
| 3 | Guide says `interrupt()`; your current doc says the graph compiles with `interrupt_before` on gate nodes. **Mixing static + dynamic interrupts** is a footgun. | §6 — migrate cleanly to dynamic `interrupt()` + `Command(resume=...)`; remove `interrupt_before`. |
| 4 | Stack lists **React 18**, but shadcn/Tailwind v4 target **React 19**. | §2 — pin React 19. |
| 5 | Observability is "**LangSmith *or* Langfuse**" (hand-wavy). For a *governed, auditable, regulated-domain* tool, data sovereignty matters. | §2 / §11 — default to **self-hosted Langfuse** (MIT, framework-agnostic). LangSmith self-host needs an Enterprise license. |
| 6 | "Native SSE with reconnection" but **no event replay** — a dropped connection silently loses cockpit events, which is unacceptable when the UI is your audit window. | §8 — event-id'd ring buffer + `Last-Event-ID` resume (now first-class in FastAPI ≥ 0.135.0). |
| 7 | Clean-slate **ignores the duplicate API surface** your doc flagged (`api/routes.py` *and* `api/work_items.py`). | §7 — consolidate to one router, delete the dead one. |
| 8 | **No migration story** for SQLite → Postgres on an existing schema. | §5 — Alembic baseline (or explicit dev-only drop/recreate). |
| 9 | "On rejection, loop back with feedback" but **no design for how feedback re-enters the run** (attempt counter, feedback field, how the agent consumes it). | §4.2 — rejection-loop state contract. |
| 10 | **No offline/stub contract.** Your `StubProvider` is actually an asset — keep a deterministic offline path per agent so the POC runs and tests with zero credentials. | §9 — formalize the offline fallback as a first-class agent contract. |
| 11 | Frontend maps stage→state; **no shared source of truth** for the state enum, so changing the machine silently breaks the UI. | §10 — generate/share the state + event types across BE/FE. |

**Bottom line:** the guide is a sound *destination*. This plan is the *route from where your code actually is* — and it patches three things the guide would otherwise let an AI assistant get wrong (idempotent interrupts, event replay, duplicate routers). Beyond hardening, **§13 adds an architecture track** — six structural moves (durable worker, hexagonal core, policy engine, ports registry, tenancy/auth, optional event-sourcing), four of them already proven in your Continuum build.

---

## 2. 2026 stack reality-check (verified, with sources)

| Area | Guide says | Verified 2026 reality | Verdict |
|---|---|---|---|
| HITL gates | `interrupt()` before every gate | `interrupt()` (dynamic) is the recommended pattern; `interrupt_before` (static) still valid but legacy. **Node re-executes from the top on resume** — pre-interrupt logic must be idempotent. | ✅ keep, **but** see §4.1 |
| Resume | (implicit) | Resume with `Command(resume=value)`; that value becomes the return of `interrupt()`; **same `thread_id`** for invoke + resume. | ✅ adopt explicitly |
| Durability | `langgraph-checkpoint-postgres`, call `setup()` | Correct. **Use a shared `AsyncConnectionPool`, not `.from_conn_string()`** (the latter exhausts connections under load). Manual conns need `autocommit=True, row_factory=dict_row`. Package is on the 1.0.x line. | ✅ keep, with pool nuance (§5) |
| Frontend | React 18 + Tailwind + shadcn/ui | shadcn/ui fully supports **Tailwind v4 + React 19** today (data-slot, OKLCH). | ⚠️ pin **React 19**, not 18 |
| Workflow viz | React Flow (xyflow) | Updated for React 19 + Tailwind v4. Fine for a node/edge graph; a custom timeline is fine too. | ✅ either |
| Observability | LangSmith *or* Langfuse | **Langfuse** = MIT, first-class self-hosting, framework-agnostic (~infra-only cost self-hosted). **LangSmith** = proprietary, self-host needs Enterprise license, zero-config for LangChain. | ⚠️ default **Langfuse** for this domain |
| SSE | Native SSE w/ reconnection | FastAPI ≥ 0.135.0 supports `Last-Event-ID` header + built-in keep-alive ping / `Cache-Control: no-cache` / `X-Accel-Buffering: no`. | ✅ keep, add replay (§8) |

Sources are listed in §13.

---

## 3. Migration philosophy: strangler-fig, not bulldozer

You are not building `pdic-pipeline` from scratch; you are **hardening a skeleton**. Rules:

1. **The app must run and demo after every workstream.** No "big bang" branch that's broken for two weeks.
2. **One workstream = one branch = one PR.** Small, reviewable diffs. (This is also how you keep an AI assistant honest — see §12.)
3. **Add the new path beside the old, flip a flag, then delete the old.** e.g. stand up the Postgres checkpointer behind `CHECKPOINTER=postgres|sqlite`, prove it, then remove SQLite-runtime.
4. **Never break the gate/resume contract.** It already works; every change must preserve "pause cleanly, resume deterministically."
5. **Tests are a ratchet.** The skeleton has phase tests — keep them green. A workstream that reds an existing test isn't done.

Recommended branch order (each gated on the prior):

```
ws1-workitem-model      → ws2-idempotent-gates     → ws3-postgres-checkpointer
ws4-api-consolidation   → ws5-sse-replay           → ws6-agent-contract+offline
ws7-cockpit-uplift      → ws8-gate-decision-ux     → ws9-observability-langfuse
ws10-shared-types       → ws11-grounding (optional)
```

This re-sequences the Master Guide so the **two correctness fixes (idempotent gates, then durable checkpointer)** land *before* the UI uplift — because a pretty cockpit over a non-resumable, double-firing backend is a liability, not a demo.

---

## 4. Cross-cutting correctness fixes (do these first — they change how every later card is written)

### 4.1 The idempotent-interrupt rule (most important fix in this document)

**Problem.** LangGraph re-executes a node *from its first line* when the run resumes after an interrupt. If your graph looks like the skeleton's likely shape:

```python
# orchestrator/graph.py — LIKELY CURRENT (dangerous)
def architect_node(state):
    adr = call_model_and_write_adr(state)   # side-effect: writes artifact / spends tokens
    decision = interrupt({"review": adr})    # ⟵ on resume, EVERYTHING above re-runs
    return route_on(decision)
```

…then every approval re-runs `call_model_and_write_adr` → duplicate ADRs, duplicate PRs, double LLM spend, corrupted audit trail.

**Rule.** A node either *does work* **or** *interrupts* — never both. Split it:

```python
# SAFE shape
def architect_node(state):                       # work node — idempotent
    if state.get("adr_done"):                     # guard: don't redo on any re-entry
        return {}
    adr = call_model_and_write_adr(state)
    return {"artifacts": {"adr": adr}, "adr_done": True}

def gate2_node(state):                            # gate node — pure interrupt, no side-effects
    decision = interrupt({"gate": "G2", "artifact_ref": state["artifacts"]["adr"]})
    return {"gate_decisions": [decision]}         # routing/feedback handled by edge from here
```

**Make every side-effect idempotent or keyed:** artifact writes overwrite by `(work_item_id, stage)`; PR/connector calls reuse an existing reference if present (`get-or-create`, never blind-create); audit appends are deduped by a deterministic event key. This is the load-bearing invariant of the whole HITL design.

### 4.2 Rejection-loop state contract

"Loop back with feedback" needs an explicit data path or it rots:

- `WorkItem` carries `attempts: dict[stage, int]` and `feedback: dict[stage, str]`.
- A `reject(reason)` decision writes `feedback[stage] = reason`, increments `attempts[stage]`, and the edge routes **back to the work node** (not the gate).
- The work node's guard from §4.1 becomes: *redo if there's newer feedback than the last artifact* (so a rejection legitimately re-runs the agent, an approval does not).
- Cap attempts (e.g. 3) → on exhaustion route to `FAILED` with a classified failure. (Mirrors what mature pipelines do.)

---

## 5. WS1–WS3: foundation (model → idempotent gates → durable checkpointer)

### WS1 — Harden the `WorkItem` model
- **AI reads:** `backend/app/models/work_item.py`, every file that imports it (grep `WorkItem`), `backend/app/db/init_db.py`.
- **Likely current state:** a Pydantic model with id / app_id / intent / state / artifacts / gate records / attempts / version pins / timestamps / failure — but probably loose typing and no clean DB mapping.
- **Changes:** promote `state` and gate-decision to **enums**; add `attempts: dict[str,int]`, `feedback: dict[str,str]` (for §4.2), `gate_history: list[GateRecord]` with `(decision, reason, actor, ts)`; add computed props the UI needs (`current_stage`, `is_blocked`, `progress_pct`). Keep it serializable both as **LangGraph state** and **DB row**.
- **Pitfall:** don't fork the model into two divergent shapes (graph vs DB). One model, one (de)serializer.
- **Done when:** existing tests pass; new enum states round-trip through persistence; UI still renders.

### WS2 — Make gates idempotent (apply §4.1)
- **AI reads:** `backend/app/orchestrator/graph.py`, `backend/app/runner/pipeline_runner.py`.
- **Likely current state:** agent work and `interrupt`/`interrupt_before` interleaved in the same node; resume re-runs side-effects.
- **Changes:** split every `agent+gate` into a **work node** (guarded, idempotent) and a **pure gate node** (`interrupt()` only); move all artifact/connector/audit writes into the guarded work node; add the `*_done` / feedback guards.
- **Pitfall:** the connector calls (`work_tracking.py`, repo/PR seam) are the dangerous ones — wrap each in get-or-create.
- **Done when:** a run that you approve, then *resume again from the same checkpoint*, produces **exactly one** artifact / PR / audit entry per stage (write a test that resumes twice and asserts no duplication).

### WS3 — Postgres checkpointer as the durable execution path
- **AI reads:** `pipeline_runner.py`, app startup (`main.py`), `db/init_db.py`, `requirements.txt`/`pyproject.toml`.
- **Likely current state:** SQLite for runtime state + an in-process or `MemorySaver` checkpointer; runs die on restart.
- **Changes:**
  1. Add `langgraph-checkpoint-postgres`; create **one** `AsyncConnectionPool(conninfo=DSN, max_size=20, kwargs={"autocommit": True, "row_factory": dict_row})` at app startup; pass it to `AsyncPostgresSaver(pool)`; `await saver.setup()` once on startup. **Do not** use `.from_conn_string()` per-run.
  2. Compile the graph with this checkpointer; thread `config={"configurable": {"thread_id": work_item_id}}` through start **and** resume.
  3. Resume via `graph.ainvoke(Command(resume=decision), config=...)` (same `thread_id`).
  4. Behind `CHECKPOINTER=postgres|sqlite` so you can fall back while migrating; on startup, **restore in-flight runs** from the checkpointer.
- **Pitfall:** connection exhaustion from per-call pools (the exact thing `.from_conn_string()` causes); forgetting `setup()`; mismatched `thread_id` between start and resume (silent "resume does nothing").
- **Done when:** start a run, hit a gate, **restart the process**, resume the gate from the UI → the run continues from the checkpoint. (This is the headline demo moment.)

---

## 6. WS2.5 — Interrupt migration note (folded into WS2)

If the current graph uses `interrupt_before=[...gate nodes...]` at compile time, replace it with in-node dynamic `interrupt()` (§4.1) and resume with `Command(resume=...)` (§5). Pick **one** mechanism. Dynamic `interrupt()` is preferred because it carries a **payload** to the UI (what to review) and a **typed resume value** back — which is exactly your gate-decision contract. Remove `interrupt_before` once migrated so no one wires a second, conflicting pause.

---

## 7. WS4 — Consolidate the duplicate API surface
- **AI reads:** `backend/app/api/routes.py` **and** `backend/app/api/work_items.py`, plus `main.py` where routers are included.
- **Likely current state:** two overlapping route modules (older + newer) — the doc flagged this as transitional debt.
- **Changes:** choose the newer surface as canonical; move any unique endpoints from the old one into it; delete the dead module; update `main.py` includes; keep paths stable (`POST /api/work-items`, `…/{id}`, `…/{id}/gate` GET+POST, `…/{id}/events`).
- **Pitfall:** the frontend `api/client.ts` may call the *old* paths — grep the frontend before deleting anything.
- **Done when:** one router, no duplicate path registrations (FastAPI will even warn), all FE calls resolve, tests green.

---

## 8. WS5 — Resumable SSE (don't lose cockpit events)
- **AI reads:** `backend/app/streaming/event_bus.py`, the `…/events` route, frontend `hooks/` (the SSE hook) and `api/client.ts`.
- **Likely current state:** a fan-out event bus + an `EventSource` on the FE; reconnect re-subscribes but **misses events emitted during the gap**.
- **Changes:**
  1. Give every SSE message a monotonic `id:` and keep a bounded **per-run ring buffer** (or read from the audit store, which is already append-only).
  2. On the `…/events` route, read the `Last-Event-ID` header (FastAPI ≥ 0.135.0) and **replay** buffered events with a higher id before streaming live ones.
  3. Lean on FastAPI's built-in keep-alive ping / `Cache-Control: no-cache` / `X-Accel-Buffering: no`.
  4. FE: ensure the hook lets `EventSource` send `Last-Event-ID` automatically (don't tear down and recreate without it).
- **Pitfall:** unbounded buffers (memory leak) — cap by count/age and fall back to "full state refetch" past the window.
- **Done when:** kill the network mid-run, restore it → the cockpit shows **no missing events** and no duplicates.

---

## 9. WS6 — Agent base contract + first real agent + offline path
- **AI reads:** `backend/app/agents/*` (especially `po_bsa`), `backend/app/model/model_provider.py` (the `StubProvider`), one existing agent end-to-end.
- **Likely current state:** agents return loosely-shaped dicts; `StubProvider` returns canned text; no enforced I/O contract.
- **Changes:**
  1. Define a `BaseAgent` with **typed Pydantic input/output contracts** and `with_structured_output(...)` on the model call.
  2. **Formalize the offline fallback:** each agent must produce a *deterministic, schema-valid* output when no model creds are present (promote `StubProvider` from "demo filler" to "the offline contract"). This is what keeps the POC runnable and testable with zero credentials — a genuine asset, not a stopgap.
  3. Add a `grounding` injection seam (no-op default) so WS11 can slot in later.
  4. Implement **PO/BSA** fully against this contract as the reference agent; leave the others on the offline path until you wire them one at a time.
- **Pitfall:** letting structured-output parsing throw on a malformed LLM response — catch and fall back to the offline contract so a bad generation degrades instead of crashing the run.
- **Done when:** PO/BSA runs live (if creds set) or offline (if not), always returns a schema-valid artifact; the run completes end-to-end on the offline path with no credentials.

---

## 10. WS7–WS8: cockpit uplift + gate-decision UX (now safe to make pretty)

### WS7 — Cockpit uplift
- **AI reads:** `frontend/src/Cockpit.tsx`, `App.tsx`, `hooks/`, `types/`, `api/client.ts`; `package.json`.
- **Likely current state:** a functional but plain cockpit; bespoke fetch + `useState`; stage→state mapping inlined.
- **Changes (incremental, not a rewrite):** introduce **Tailwind v4 + shadcn/ui** (canary CLI), pin **React 19**; adopt **TanStack Query** for run/list/detail fetching (kill ad-hoc polling races); add a **Live Activity Feed** (LangSmith-style trace rows: agent action / tool call / state change / timing) fed by WS5's replayable SSE; add **workflow visualization** (React Flow graph *or* a timeline) driven by the shared state enum (§10/WS10); subtle Framer Motion on state transitions.
- **Pitfall:** don't rewrite `Cockpit.tsx` wholesale — wrap/extract components so the working SSE + run-selection logic survives.
- **Done when:** the cockpit shows live agent activity with drill-down, stage progress, and timing — and still works when the backend restarts (proves WS3+WS5).

### WS8 — Gate-decision panel
- **AI reads:** the gate components in `frontend/src/`, the `…/{id}/gate` GET/POST client calls.
- **Changes:** a prominent `GateDecisionPanel` — what needs review (link to full artifact), key context from the prior agent, **Approve / Reject(+reason) / Request-changes** buttons, prior-decision history. POST maps to the `Command(resume=...)` payload (§5); reject feeds §4.2.
- **Pitfall:** the decision payload shape **must** match what the gate node's `interrupt()` expects on resume — keep them in one shared type (§10).
- **Done when:** approving advances the run; rejecting with a reason loops back to the agent, increments `attempts`, and the agent re-runs with the feedback — visibly, in the cockpit.

---

## 11. WS9 — Observability: self-hosted Langfuse
- **AI reads:** `backend/app/observability/tracing.py`, app startup.
- **Likely current state:** OpenTelemetry init with an optional OTLP exporter; not wired to a backend.
- **Changes:** default to **self-hosted Langfuse** (MIT, data-sovereign, framework-agnostic — the right call for a governed/regulated pipeline). Trace each agent node, each gate decision, each connector call; record **real token cost** (not a stub) and **per-stage latency**. Keep OTLP as the transport so you're not locked in. (LangSmith remains an option if you go all-in on LangChain and accept the Enterprise self-host license — but it's not the default here.)
- **Pitfall:** PII in traces — for a regulated domain, scrub intent/artifact bodies or keep them in your own audit store, not the trace.
- **Done when:** a run shows up as a full trace (nodes, gates, tokens, latency); "what did stage X cost / how long did it take" is answerable from the dashboard.

---

## 12. WS10 — One source of truth for states & event shapes
- **AI reads:** the backend state enum (`models/work_item.py`), the event-type constants (`streaming/`), and the frontend `types/`.
- **Changes:** define the state machine and event/gate-decision shapes **once** and share them — either generate FE TS types from the Pydantic models (e.g. an OpenAPI/`datamodel-codegen` step in the build) or keep a single hand-maintained contract file the FE imports. The stage→state mapping in the cockpit reads from this, not a hard-coded copy.
- **Done when:** changing a state name in the backend forces a typed FE update (build fails loudly) instead of silently breaking the cockpit.

*(WS11 — Grounding layer: optional, slots into the WS6 seam. Repo/knowledge context for the Architect/Developer agents. Defer until the core loop is durable.)*

---

## 13. Architecture track — bigger moves (you said "feel free")

The WS cards above harden the skeleton in place. These six are **structural** — they change the shape of the system, not just its correctness. Run them as a **parallel track** that the feature workstreams ride on: the two cheap, pure-refactor ones (A3 + A4) fold into WS1/WS2 early; the rest are staged so the app stays runnable. Where a pattern is already **proven in Continuum** (your other implementation), I say so — you can lift the approach rather than re-derive it.

### A1 — Thin API + durable worker (queue-worker execution), *not* in-process
- **Current shape (assumed):** the pipeline runs inside the API process (a FastAPI `BackgroundTask` or inline in the runner). A 3-day human gate holds a process; a deploy/crash mid-run is fragile; one slow run starves others; you can't scale execution independently of the API.
- **Change:** the **API becomes thin** — it only *enqueues commands* (`start`, `resume(decision)`) and serves reads. A separate **worker pool** consumes a durable queue and drives the LangGraph runs. The Postgres checkpointer (WS3) is the shared state, so **any worker resumes any run by `thread_id`**. The queue can be **Postgres-backed (`SELECT … FOR UPDATE SKIP LOCKED`)** so you add *no* new infra, or Redis/RabbitMQ if you already run one.
- **Why it's the keystone:** this is what makes "a gate that waits days" and "survive a deploy" actually true. It is *enabled by* WS3 — do it right after.
- **Trade-off:** one more process to run/deploy; messages must be idempotent (ties straight back to §4.1).
- **Strangler-fig:** stand the worker up beside the in-process path behind `EXECUTION=inproc|worker`; flip when proven; delete `inproc`.

### A2 — Audit log as the source of truth (event-sourced WorkItem)
- **Current shape:** append-only audit is a *side-write*; the `WorkItem` row is primary.
- **Change:** append typed **domain events** (`SpecDrafted`, `GateApproved{actor,reason,ts}`, `ChangeRequested`, `RunFailed{class}`…) to the append-only store as the **write model**; the `WorkItem` becomes a **projection** you can rebuild from the log. Reads/UI hit projections (CQRS-lite).
- **Why:** this is the *literal* definition of the auditability the product sells — provable who/what/when/why, time-travel, rebuildable read models.
- **Trade-off:** real complexity; easy to over-build. **Deferrable** — your append-only audit already buys ~80% of the win. **Recommendation:** adopt the *event vocabulary* now (typed domain events flowing to both audit and SSE), full event-sourcing later, only if a regulator actually demands replay.

### A3 — Hexagonal core: pure domain, LangGraph as an adapter *(do early — cheap, huge payoff)*
- **Current shape:** LangGraph types and domain logic are interwoven in `orchestrator/graph.py`.
- **Change:** extract a **pure-Python domain core** — `WorkItem`, the state machine, the policy engine — that imports *nothing* from LangGraph / FastAPI / the DB. LangGraph becomes a thin **adapter** that calls the core; FastAPI and persistence are adapters too (ports & adapters / hexagonal).
- **Why:** testable without spinning the engine, swappable orchestrator, no circular imports, governance logic auditable in isolation.
- **Proven in Continuum:** its `state_machine.py` imports nothing from the orchestrator and `policy_engine` reads the artifact via `getattr`, so the state machine is **pure data + pure predicate** with zero cycles. Lift that structure.
- **Strangler-fig:** pure extraction, no behavior change — the safest big move. Fold into WS1.

### A4 — Explicit policy engine (separate *allowed* from *executed*) *(fold into WS2)*
- **Current shape:** routing/governance rules are hard-coded in graph edges.
- **Change:** a pure predicate `can_transition(work_item, from_state, to_state) -> (ok, reason)` that checks, in order: **edge-exists → human-gate-granted → required-artifacts-present → quality-gates-green**. The graph *asks* the policy engine instead of hard-coding routing. Name the gates **G1–G5** and map each to the exact edge it governs.
- **Why:** governance becomes declarative, auditable, and per-tenant configurable; "why did / can't this advance?" is answerable deterministically — which is what makes the compliance story defensible.
- **Proven in Continuum:** this is its M13 — `state_machine.py` *declares* the graph + named gates; `policy_engine.can_transition()` *enforces* it as a pure predicate. Same split applies 1:1 to your 5-gate flow.

### A5 — Connectors as typed ports + per-tenant adapter registry *(alongside WS6)*
- **Current shape:** connector seams with offline stubs (`connectors/work_tracking.py`, repo/PR, pipeline).
- **Change:** define typed **Ports** (`WorkTrackingPort`, `RepoPort`, `PipelinePort`, `DocsPort`) and a **registry** that resolves the concrete adapter from tenant/config at runtime. The offline stub becomes *just the default adapter*, not a special case.
- **Why:** delivers the strategic "app-agnostic / tooling-agnostic" claim for real, enables per-tenant integrations, and keeps offline testing trivial (the stub is a first-class adapter).
- **Trade-off:** low — it's formalizing seams you already have.

### A6 — Multi-tenancy + real approver identity *(its own track, after the loop is durable)*
- **Current shape:** process-scoped, single-user; gate decisions carry no real principal.
- **Change:** tenant isolation on every read/write (per-tenant data partition / audit / spec store), **RBAC** (dev / reviewer / admin), and a **real approver identity + timestamp captured on every gate decision**.
- **Why:** non-negotiable for the regulated-domain pitch — "who approved this" must be a real principal, not anonymous. It also makes A2's `GateApproved{actor}` events meaningful.
- **Proven in Continuum:** this is its P0.3 — a leaf `auth/` package (imports nothing from the core), an RBAC matrix, `tenant_key()` namespacing, approver recorded per gate, and **offline-safe by construction** (auth unset → a single dev-admin in the `default` tenant, so nothing else changes). Copy that seam exactly.
- **Trade-off:** touches every endpoint; sequence it after the core loop is durable so you're not securing a moving target.

**What to *not* over-build (yet):** A2 full event-sourcing and A1's external-broker variant are the ones to stage carefully — start A1 on a Postgres-backed queue (no new infra) and A2 as just a typed event vocabulary. **A3 + A4 are pure refactors with outsized payoff — do them early.** A5 is cheap. A6 is mandatory but later.

**Architecture-track sequencing (overlaid on the WS order):**

```
WS1 ─┬─ A3 hexagonal core extraction        (pure refactor, no behavior change)
WS2 ─┴─ A4 policy engine as routing source  (declarative governance)
WS3 ──── (Postgres checkpointer)
        └─ A1 thin API + durable worker      (needs the checkpointer)
WS6 ──── A5 ports + per-tenant adapter registry
post-loop ─ A6 tenancy + auth + approver identity
optional/last ─ A2 full event-sourcing       (only if replay is actually required)
```

---

## 14. How an AI coding assistant will actually execute this

You asked specifically how Copilot / Cursor / Claude *interpret existing code and make changes* — design the prompts to match how they work:

1. **They don't see the whole repo — they read what you point them at (or grep for).** So every task card above **names the files first**. Start each prompt with *"Read `X`, `Y`, `Z` and summarize the current behavior before changing anything."* Make it prove it understood the current code before editing.
2. **They pattern-match against what's already there.** If `graph.py` interleaves work + interrupt, an assistant told "add a gate" will copy that broken shape. So the prompt must state the **invariant** (§4.1: "a node either does work or interrupts, never both") — not just the goal.
3. **They edit in place; small diffs are safer.** Drive **one workstream per branch/PR** (§3). Don't paste WS1–WS11 at once; paste one card, review the diff, run tests, commit, next.
4. **They will happily "improve" by rewriting.** Constrain it: *"Modify in place. Do not rewrite `Cockpit.tsx`/`graph.py` wholesale. Preserve the working SSE/gate-resume logic. Show me the diff."*
5. **Give them the acceptance test up front.** Each card ends with "Done when…" — paste that as the definition of done so the assistant writes/runs the test, not just the code.
6. **Make them flag assumptions.** End prompts with *"If the actual file differs from what this plan assumes, stop and tell me the delta before editing."* (This plan assumes the structure from your architecture doc; reality may differ.)

**Reusable prompt skeleton (paste per workstream):**

```
You are improving the EXISTING pdic-pipeline (do not rebuild from scratch).
WORKSTREAM: <WSn title>
1. First READ: <files from the card>. Summarize their current behavior. Do not edit yet.
2. INVARIANT you must preserve: <e.g. §4.1 idempotent-interrupt rule; gate/resume contract>.
3. CHANGE: <changes from the card>. Modify in place; keep diffs minimal; don't rewrite working files wholesale.
4. PITFALLS: <pitfalls from the card>.
5. DONE WHEN: <acceptance test>. Write/adjust a test that proves it and run the existing suite.
If the real files differ from these assumptions, STOP and report the delta before editing.
```

---

## 15. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| AI follows guide literally → re-firing side-effects on resume | High | High (dup PRs, double spend, corrupt audit) | §4.1 invariant stated in every gate-touching prompt; WS2 double-resume test |
| Connection exhaustion from per-run Postgres pools | Med | High (prod outage) | §5 single `AsyncConnectionPool`; never `.from_conn_string()` per call |
| `thread_id` mismatch → "resume does nothing" | Med | High (stuck runs) | §5 thread `thread_id=work_item_id` through start+resume; assert in runner |
| Cockpit rewrite breaks working SSE/gate logic | Med | Med | §14 "modify in place, no wholesale rewrite"; one PR per WS |
| SSE event loss on reconnect mistaken for "backend bug" | Med | Med | WS5 replay + Last-Event-ID; network-drop test |
| FE/BE state enum drift | Med | Med | WS10 shared/generated types |
| Migrating SQLite→Postgres loses local data | Low | Low (dev only) | WS3 dual-mode flag; Alembic baseline or documented drop/recreate |
| **A1 worker:** message redelivery double-drives a run | Med | High | Idempotent command handling (§4.1); `thread_id`-keyed dedupe; `FOR UPDATE SKIP LOCKED` claim |
| **A2 event-sourcing** over-engineered for a POC | Med | Med (lost time) | Start with typed event *vocabulary* only; defer full sourcing until replay is required |
| **A3/A4 refactor** quietly changes routing behavior | Low | High | Pure extraction — assert identical run traces before/after on the offline path |
| **A6 auth** rollout breaks offline/dev runs | Med | Med | Copy Continuum's offline-safe seam: auth unset → single dev-admin, byte-unchanged |

---

## 16. Sequenced summary (what to do, in order)

**Feature track (harden in place):**

1. **WS1** WorkItem model hardening (enums, attempts/feedback, one (de)serializer). — *fold in A3 hexagonal extraction*
2. **WS2 (+WS2.5)** Idempotent work/gate split; migrate to dynamic `interrupt()`. ← correctness — *fold in A4 policy engine*
3. **WS3** Postgres checkpointer via shared async pool; restart-survives-resume demo. ← durability — *then A1 worker*
4. **WS4** Collapse duplicate API routers to one.
5. **WS5** Resumable SSE (id'd events + `Last-Event-ID` replay).
6. **WS6** Agent base contract + structured outputs + formal offline fallback; PO/BSA first. — *fold in A5 ports/registry*
7. **WS7** Cockpit uplift (Tailwind v4 / shadcn / React 19 / TanStack Query / live feed + viz).
8. **WS8** Gate-decision panel wired to `Command(resume=...)` + rejection loop.
9. **WS9** Self-hosted Langfuse traces (real cost + latency).
10. **WS10** One shared source of truth for states/events.
11. **WS11** *(optional)* Grounding layer into the WS6 seam.

**Architecture track (structural — §13):** A3 hexagonal core + A4 policy engine (early, pure refactors) → A1 thin-API/durable-worker (right after WS3) → A5 ports + per-tenant registry (with WS6) → A6 tenancy/auth/approver identity (after the loop is durable) → A2 full event-sourcing (last, optional).

The reorder vs. the Master Guide is deliberate: **correctness (idempotent gates) and durability (Postgres checkpointer) land before the UI uplift**, because a polished cockpit over a non-resumable, double-firing backend is a demo that breaks the moment someone restarts the process or approves a gate twice. The architecture track front-loads the two *pure refactors* (A3+A4) because they cost almost nothing and make every later change cleaner.

---

## 17. Sources (verified June 2026)

- LangGraph interrupts / re-execution on resume — [LangChain docs: Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) · [DeepWiki: HITL & Interrupts](https://deepwiki.com/langchain-ai/langgraph/3.7-human-in-the-loop-and-interrupts) · [interrupt() reference](https://reference.langchain.com/python/langgraph/types/interrupt)
- `Command(resume=...)` pattern — [LangChain docs: Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) · [BSWEN: interrupt() pattern (2026)](https://docs.bswen.com/blog/2026-04-16-langgraph-human-in-the-loop/)
- Postgres checkpointer / async pool / `setup()` — [LangGraph Persistence Guide (2026)](https://fast.io/resources/langgraph-persistence/) · [AsyncPostgresSaver reference](https://reference.langchain.com/python/langgraph.checkpoint.postgres/aio/AsyncPostgresSaver) · [existing-pool gist](https://gist.github.com/MSNP1381/68cb803fd3f8010b1ddd80d3a6d66a05) · [pypi: langgraph-checkpoint-postgres](https://pypi.org/project/langgraph-checkpoint-postgres)
- Tailwind v4 + React 19 + shadcn/ui — [shadcn/ui: Tailwind v4](https://ui.shadcn.com/docs/tailwind-v4) · [shadcn/ui: React 19](https://ui.shadcn.com/docs/react-19) · [React Flow: React 19 + Tailwind v4](https://reactflow.dev/whats-new/2025-10-28)
- Langfuse vs LangSmith — [Langfuse: LangSmith alternative](https://langfuse.com/faq/all/langsmith-alternative) · [LangSmith vs Langfuse (2026)](https://myengineeringpath.dev/tools/langsmith-vs-langfuse/)
- FastAPI SSE + `Last-Event-ID` — [FastAPI: Server-Sent Events](https://fastapi.tiangolo.com/tutorial/server-sent-events/)
