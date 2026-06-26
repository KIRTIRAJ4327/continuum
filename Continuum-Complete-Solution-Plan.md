# Continuum — Complete Solution Plan
## From Working POC to Production-Grade Agentic SDLC Platform

| | |
|---|---|
| **Date** | June 25, 2026 |
| **Baseline** | M0–M13, P0.1, P0.2, P1.1 — all shipped and verified |
| **Purpose** | Full analysis of what makes Continuum a complete solution + dispatch prompts |

---

## Part 1 — Completeness Analysis

### What "complete" actually means

A complete agentic SDLC platform has five properties, and Continuum's current
position against each one:

```
PROPERTY 1: Correctness
  The system does exactly what it claims. Every resume is safe, every gate
  fires exactly once, every agent runs exactly once per stage.
  Current: 85% — interrupt() structure is good but needs hardening;
           SSE has no monotonic IDs; reconnect loses position.

PROPERTY 2: Observability
  An operator can see what is happening right now and what happened before.
  Current: 60% — events exist, SSE works, but no Last-Event-ID replay,
           no token-level streaming, no agent-to-agent trace timeline.

PROPERTY 3: Trustworthiness
  Every claim the system makes is literally true and independently verified.
  Current: 90% — Evidence Stack is real after P0.2, mapping fidelity is real,
           compliance report is structured. Gap: P0.2 uses local-exec
           (not hardened Hyper-V isolation).

PROPERTY 4: Operability
  Real users can use it. Auth, tenancy, notifications, multi-repo.
  Current: 30% — single user, no RBAC, no identity, no notifications,
           no webhook triggers. This is the largest gap.

PROPERTY 5: Extensibility
  Adding a new app, a new agent, a new gate, or a new skill takes minutes,
  not days. The platform is the product, not the demo.
  Current: 75% — D33 two-layer split shipped (M8), skill versioning works,
           the .pdlc/ pattern is there. Gap: no self-serve app onboarding,
           no webhook integration, no Slack/Teams notification.
```

### The gap map

```
┌─────────────────────────────────────────────────────────────────────┐
│  DONE ✅                          GAPS ❌                           │
├─────────────────────────────────────────────────────────────────────┤
│  15-state machine                 Auth + tenancy (P0.3)             │
│  Policy engine                    SSE Last-Event-ID (C1)            │
│  Evidence Stack (real, P0.2)      Monotonic event IDs (C1)          │
│  Spec Registry (M11)              AsyncConnectionPool (C1)          │
│  Compliance Report (M12)          Command(resume=) semantics (C1)   │
│  Durable execution (P0.1)         Token streaming (C2)              │
│  Box Lite isolation (P0.2)        Observability cockpit (C2)        │
│  Gate independence (P1.1)         Notification system (C3)          │
│  Mapping Fidelity (M7)            Webhook triggers (C3)             │
│  Evolution Agent (M5)             ACA/Hyper-V sandbox (P0.2 follow) │
│  Memory + GraphRAG (M3)           Multi-repo support (C3)           │
│  MAF pilot (M9)                   React 19 + Langfuse (C4)          │
│  ASSERT/Rubric (M10)              Self-serve onboarding (C4)        │
└─────────────────────────────────────────────────────────────────────┘
```

### Priority ordering

The gaps fall into four tracks that can run in parallel after C1:

```
C1 — CORRECTNESS   (1 session, blocks everything)
  interrupt() hardening, AsyncConnectionPool, Command(resume=),
  monotonic SSE event IDs, Last-Event-ID replay, useSSE reconnect

C2 — OBSERVABILITY (2 sessions, high demo value)
  Typed event vocabulary, token streaming, TraceTimeline,
  WorkflowGraph live update, GateDecisionPanel, LogsPanel

P0.3 — OPERABILITY (3 sessions, gates real-client use)
  Auth/identity, RBAC, per-tenant isolation, audit attribution

C3 — EXTENSIBILITY (2 sessions, platform scale)
  Webhook triggers, Slack/Teams notifications, multi-repo,
  self-serve .pdlc/ app onboarding

C4 — POLISH (1 session, quality of life)
  React 19, self-hosted Langfuse, duplicate API router cleanup,
  ACA sandbox backend
```

---

## Part 2 — C1: Correctness Sprint

### What's actually broken (honest diagnosis)

Reading the live code in `orchestrator/graph.py`:

**The good news:** The `interrupt()` structure is better than the PR implies.
The orchestrator (`_route`) is a separate node from the gate (`_human_gate_node`).
`_route` sets `human_approval_pending=True` and returns. `_human_gate_node` then
calls `interrupt()`. This is close to the correct pattern.

**The real gaps:**

**Gap 1 — No monotonic `seq` on events.** Events are emitted as dicts
`{event_type, agent, run_id, data, timestamp}`. No `id` field. The SSE
endpoint doesn't emit `id: N\n` frames. The `useSSE` hook never sends
`Last-Event-ID`. A browser disconnect → reconnect replays ALL history from
index 0. Fine now (history is small), not fine when a run has 200 events.

**Gap 2 — `AsyncPostgresSaver` opens a bare `psycopg.AsyncConnection` per
graph instance** (`await psycopg.AsyncConnection.connect(...)`). For concurrent
runs on the same graph this is a shared single connection, not a pool.
Under concurrency this becomes a bottleneck or a bug.

**Gap 3 — Resume uses `_execute_pipeline()` not `Command(resume=...)`.** The
`resume_run()` API endpoint re-drives `_execute_pipeline()` in a background
task. This is the in-memory path. The LangGraph `ContinuumGraph.run()` path
(which uses the durable checkpointer) has no resume endpoint wired to it.
`P0.1` added `RunStore` but the API still routes to `_execute_pipeline()`.

**Gap 4 — `_human_gate_node` has no idempotency guard.** If resumed twice
(e.g., double-submit of the approve button), it runs the approval logic twice.
`story_approved` gets set to `True` twice — harmless now, but a pattern that
could cause issues as the gate logic grows.

### C1 Dispatch Prompt

```
Branch: git checkout dev && git checkout -b fix/c1-correctness

GOAL: Four correctness fixes, one session.
All 16 non-negotiable checks must pass after every change.

## FIX 1 — Monotonic event IDs + Last-Event-ID SSE replay

orchestrator/events.py:
- Add a per-run monotonic counter to EventBus:
    self._seq: Dict[str, int] = {}  (starts at 0 per run)
- In emit(): before appending to log, add seq to event:
    event = {**event, "seq": self._seq.get(run_id, 0)}
    self._seq[run_id] = event["seq"] + 1
- seq must be set ONCE on emit, never mutated. Events in _log already
  carry their seq. Do not re-number on replay.

api/main.py — stream_events():
- Read the optional Last-Event-ID header:
    last_id = request.headers.get("last-event-id")
    resume_seq = int(last_id) + 1 if last_id and last_id.isdigit() else 0
- On replay, skip events with seq < resume_seq:
    for ev in history:
        if ev.get("seq", 0) >= resume_seq:
            yield f"id: {ev['seq']}\ndata: {json.dumps(ev)}\n\n"
- On live emit, include the id field:
    yield f"id: {ev.get('seq', '')}\ndata: {json.dumps(ev)}\n\n"
- Add Request as parameter to stream_events (FastAPI injects it):
    async def stream_events(run_id: str, request: Request) -> StreamingResponse

ui/src/hooks/useSSE.ts:
- Track lastSeq: useRef<number>(-1)
- On each message: lastSeq.current = ev.seq ?? lastSeq.current
- On onerror: don't just set error state — attempt reconnect after 2s:
    setTimeout(() => {
      if (lastSeq.current >= 0) {
        // reconnect with Last-Event-ID — browser EventSource does this
        // automatically when you close and reopen with the same URL
        // IF you set eventSource.withCredentials or use the native retry.
        // Simplest: close + reopen; the id: frame sets the browser's
        // lastEventId which is sent automatically on reconnect.
      }
    }, 2000)
  Actually: EventSource automatically sends Last-Event-ID on reconnect
  when the server has sent id: frames. Just set the onerror handler to
  NOT close the connection — let the browser retry automatically.
  Remove the es.close() call in onerror. Add a reconnecting: boolean state.

ui/src/types.ts:
- Add seq: number to AgentEvent interface

## FIX 2 — AsyncConnectionPool for Postgres checkpointer

orchestrator/graph.py — async_init():
Replace:
    conn = await psycopg.AsyncConnection.connect(self._checkpointer_uri)
    self.checkpointer = AsyncPostgresSaver(conn)

With:
    from psycopg_pool import AsyncConnectionPool
    pool = AsyncConnectionPool(
        self._checkpointer_uri,
        min_size=1, max_size=5,
        open=False,
    )
    await pool.open(wait=True)
    self.checkpointer = AsyncPostgresSaver(pool)

Add psycopg-pool to requirements: psycopg-pool>=3.2
Wrap the import in try/except ImportError — if psycopg_pool is absent,
fall back to the single-connection path (offline path unchanged).
Log a WARNING if falling back: "psycopg-pool unavailable; using single
connection — not suitable for concurrent runs".

## FIX 3 — idempotency guard on _human_gate_node

orchestrator/graph.py — _human_gate_node():
Add a guard at the TOP of the function, before the interrupt() call:
    # Idempotency: if this gate was already resolved in a prior invocation
    # (e.g., double-resume), skip the interrupt() entirely.
    gate_name = state.approval_gate_name or "unknown"
    already_resolved = (
        (gate_name == "story_review" and state.story_approved) or
        (gate_name == "design_review" and state.design_approved) or
        (gate_name == "merge_review" and state.merge_approved) or
        (gate_name not in ("story_review","design_review","merge_review")
         and not state.human_approval_pending)
    )
    if already_resolved:
        logger.info("[HUMAN_GATE] Gate '%s' already resolved — skipping", gate_name)
        state.human_approval_pending = False
        state.approval_gate_name = None
        return state

## FIX 4 — Wire Command(resume=...) to the API resume path

api/main.py — resume_run():
Add an alternative path when ContinuumGraph is available (POSTGRES_DSN set):
    # P0.1 follow-up: if the graph-based path is active, use Command(resume=)
    # instead of re-driving _execute_pipeline(). This makes the LangGraph
    # checkpointer the source of truth for durable runs.
    graph = _GRAPH  # module-level Optional[ContinuumGraph], set at startup
    if graph is not None and dsn:
        from langgraph.types import Command
        thread = {"configurable": {"thread_id": run_id}}
        decision = {"approved": approved}
        async def _graph_resume():
            await graph.graph.ainvoke(
                Command(resume=decision), config=thread
            )
        asyncio.create_task(_graph_resume())
        return {"status": "resumed", "run_id": run_id, "approved": approved}
    # fallback: existing _execute_pipeline() path (offline / no POSTGRES_DSN)
    ... existing logic unchanged ...

Add at module startup (after RunStore.create()):
    _GRAPH: Optional[ContinuumGraph] = None
    dsn = os.getenv("POSTGRES_DSN", "").strip()
    if dsn:
        from orchestrator.graph import ContinuumGraph
        _GRAPH = await ContinuumGraph.create(dsn)

## VERIFY SCRIPT — scripts/verify_c1_correctness.py (new, 4/4)

case A: emit 3 events → check seq values are 0, 1, 2
case B: subscribe with resume_seq=1 → only events with seq>=1 replayed
case C: _human_gate_node called twice (double-resume sim) → state updated once
case D: EventBus purge → history cleared, seq counter reset

Makefile: add verify-c1 target.
Add verify_c1_correctness.py to CLAUDE.md non-negotiable list.

## CONSTRAINTS
- All 16 non-negotiable checks still pass
- Offline path unchanged (no psycopg_pool, no POSTGRES_DSN needed)
- seq field is additive — existing event consumers ignore unknown fields
- Command(resume=) path only activates when POSTGRES_DSN is set
- NEVER break the _execute_pipeline() fallback path

## DONE WHEN
- make verify-c1 → 4/4 PASS
- SSE stream emits id: N frames for every event
- Browser reconnect after disconnect replays only missed events
- Double-approve does not double-set story_approved
- make verify-offline → 11/11 + 3/3 PASS (all 16 checks)
```

---

## Part 3 — C2: Observability Cockpit

### What's missing

The backend emits `agent_start`, `agent_complete`, `gate_green`, `gate_red`,
`gate_retry`, `human_gate_pending`, `human_gate_resolved`, `run_complete`,
`run_blocked`, `run_returned`, `pdlc_written`.

Missing event types that the §13 cockpit requires (from the PR's plan doc):

```
tool_call       — which tool the agent invoked, arguments, result
llm_token       — streaming token (enables live typewriter output)
agent_thinking  — intermediate reasoning before a decision
agent_milestone — named checkpoint within a long agent run
artifact_ready  — artifact written to state (with preview)
sensor_result   — individual gate sensor result (ruff/mypy/pytest/bandit each)
scope_checked   — mapping fidelity result attached
evidence_built  — all 6 evidence layers assembled
controlled_hold — run parked (blocked), reason, what the human sees
```

Missing UI components:
```
TraceTimeline   — horizontal timeline of agent handoffs, gate decisions
WorkflowGraph   — live update of which node is active (extend AgentGraph.tsx)
LogsPanel       — 3-tier log (agent_thinking / decided / milestone)
GateDecisionPanel — spec + diff + evidence side-by-side at gate time
RunMetricsBar   — cost / lead-time / first-pass / retry-count in header
```

### C2 Dispatch Prompt

```
Branch: git checkout dev && git checkout -b feature/c2-observability

GOAL: Make the backend visible in the UI. People expect to see agents
working, decisions being made, and handoffs happening — not a spinner.
This is the "alive" feeling that wins demos.

## TASK 1 — Richer event vocabulary (orchestrator/events.py + emitters)

Add these event_type strings to the existing dict-based event system
(no enum, consistent with existing convention). Define their shape in
a comment block near the existing event type documentation:

    # NEW EVENT TYPES (C2)
    # tool_call     {tool_name, args_preview, result_preview, duration_s}
    # llm_token     {token, cumulative_tokens}
    # agent_thinking {thought}   — reasoning step before action
    # agent_milestone {message}  — named checkpoint (e.g. "4/4 tests passed")
    # artifact_ready {agent, artifact_type, preview}
    # sensor_result  {sensor, status, detail}   — per gate sensor
    # scope_checked  {exact_match, extra_in_code, missing_in_code}
    # evidence_built {layers: [{layer, status, detail}]}
    # controlled_hold {reason, gate, retry_count}

Add seq field (from C1) is already there after C1 lands.

Emit these events from their natural locations:

orchestrator/agent_runner.py:
- In _run_llm() tool-call loop: emit tool_call after each tool response
- In _run_llm(): if streaming is enabled, emit llm_token per chunk
  (add CONTINUUM_STREAM_TOKENS env flag, default off — token events
  are high-volume and optional)
- In _run_offline(): emit agent_milestone for each key step
  (e.g. "Spec written", "4 files planned", "PR opened")

orchestrator/gates.py:
- After each individual sensor (gate_lint, gate_typecheck, gate_test,
  gate_sast): emit sensor_result with the real ExecResult detail

skills/scope_guard/v1.0/skill.py:
- After _check_scope(): emit scope_checked with the fidelity result

evals/evidence_stack.py:
- After build_evidence_stack(): emit evidence_built with all 6 layers

## TASK 2 — ui/src/types.ts: extend EventType and add new interfaces

Add all new event_type strings to the EventType union.
Add interfaces:
    SensorResult { sensor: string; status: 'pass'|'fail'; detail: string }
    ToolCall { tool_name: string; args_preview: string; result_preview: string; duration_s: number }
    TraceStep { seq: number; event_type: EventType; agent: string; timestamp: number; label: string }

## TASK 3 — ui/src/components/TraceTimeline.tsx (new)

Horizontal timeline of agent handoffs and gate events for a run.
Input: AgentEvent[].
Render: one pill per agent/gate in sequence order (by seq field).
Active node: pulsing ring (CSS animate-pulse). Done: filled green circle.
Gate nodes: amber diamond. Blocked/returned: rose X.
Clicking a step shows its event data in a tooltip or side panel.
Use Unicode glyphs for icons (no lucide-react — repo convention).

## TASK 4 — ui/src/components/LogsPanel.tsx (new)

3-tier log display consuming AgentEvent[]:
  Tier 1 (dim, small font): agent_thinking — raw reasoning
  Tier 2 (normal): tool_call, sensor_result, scope_checked
  Tier 3 (bold/highlighted): agent_milestone, artifact_ready,
                              human_gate_pending, gate_green, gate_red

Each entry: [HH:MM:SS] [agent] message (from event data)
Auto-scroll to bottom. Show blinking cursor (▋ animate-pulse) while running.
"What the agent is NOT permitted to do" row at the bottom (reassurance):
  ✗ Cannot merge without your approval
  ✗ Cannot deploy without your approval  
  ✗ Cannot modify this pipeline's rules
This row is always visible when a run is active.

## TASK 5 — ui/src/components/RunMetricsBar.tsx (new)

Slim bar shown at the top of a run's detail view:
  First-pass: Yes / No
  Lead time: 3m 12s (live, ticking while running)
  Model cost: $0.42 (from run_status.cost_usd)
  Retry count: 0 (from state)
  Evidence: 6/6 (from evidence layers when built)

## TASK 6 — ui/src/App.tsx: wire new components

Add TraceTimeline above the existing AgentGraph (or as a tab alternative).
Add LogsPanel in the right panel below ArtifactViewer.
Add RunMetricsBar at the top of the run detail view.
GateInbox: show sensor_result events inline while gate is pending
(so the reviewer sees live sensor output as it arrives, not after).

## TASK 7 — Emit milestone events on the offline path

In _run_offline() for each agent, emit agent_milestone events so the
offline verify suite produces real event streams. This makes the
LogsPanel work in demo mode without live Azure credentials.

## CONSTRAINTS
- All 17 non-negotiable checks still pass (C1 adds verify-c1)
- llm_token events are opt-in (CONTINUUM_STREAM_TOKENS=1) — default off
- New UI components: no new npm dependencies (Tailwind + existing only)
- Unicode glyphs only (✓ ✗ ↺ ⏸ ▲ ↩ ○ ▋)
- Offline path emits enough events to make UI meaningful without Azure

## DONE WHEN
- make verify-c1 → 4/4 PASS (unchanged)
- make verify-offline → 11/11 + 3/3 PASS
- A run emits sensor_result events for each gate sensor
- LogsPanel renders 3 tiers from a live or replayed event stream
- TraceTimeline shows agent sequence with gate diamonds
- RunMetricsBar shows live cost + lead time (ticking)
- "Not permitted" row visible during any active run

Branch: feature/c2-observability → PR → dev
```

---

## Part 4 — P0.3: Auth + Tenancy

### Why this is the production gate

Currently: one process, one `_RUNS` dict, no identity. If two users submit
runs simultaneously, they share the same dict. A reviewer approving gate 1
on their run could accidentally see another user's gate. Audit trail says
"You approved" — not which human. This is unacceptable for a regulated
industry platform and even for a two-person team.

### What P0.3 means concretely

```python
# Every request carries an identity
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    user = verify_token(token)  # JWT decode, offline: static dev user
    request.state.user = user
    return await call_next(request)

# Every run is owned by a tenant + user
@dataclass
class RunOwnership:
    tenant_id: str
    user_id: str
    user_email: str
    role: Literal["developer", "reviewer", "admin"]

# Every gate approval is attributed
audit_entry = {
    "actor": user.email,
    "actor_id": user.id,
    "tenant_id": tenant.id,
    "action": "gate_approved",
    "gate": gate_name,
    "timestamp": time.time(),
}

# Neo4j and Postgres are partitioned by tenant_id
# No query touches another tenant's data
```

### P0.3 Dispatch Prompt

```
Branch: git checkout dev && git checkout -b feature/p03-auth-tenancy

GOAL: Identity on every run and gate approval, RBAC, per-tenant
data isolation. This is the gate for real-client use.

## TASK 1 — auth/identity.py (new)

Minimal JWT-based identity, offline-safe:

@dataclass
class User:
    id: str
    email: str
    tenant_id: str
    role: Literal["developer", "reviewer", "admin"]

def verify_token(token: str) -> User:
    """
    Live: decode a JWT (HS256, secret from CONTINUUM_JWT_SECRET).
    Offline / dev: if token == "dev" or is empty and DEV_MODE=1,
    return a fixed dev user:
        User(id="dev-user", email="dev@continuum.local",
             tenant_id="dev-tenant", role="admin")
    Never raise — return the dev user as fallback in dev mode.
    """

Middleware (api/main.py):
    from auth.identity import verify_token, User
    
    async def _get_user(request: Request) -> User:
        token = request.headers.get("Authorization","").removeprefix("Bearer ")
        return verify_token(token or "dev")
    
    # Inject as FastAPI dependency on all non-health endpoints

## TASK 2 — Tenant isolation on _RUNS

api/main.py:
- _RUNS key changes from run_id to f"{tenant_id}:{run_id}"
  (or keep run_id as UUID which already has negligible collision risk,
  but add tenant_id to the RunSummary + RunStore record)
- list_runs() filters to current user's tenant_id
- get_run(), resume_run(), reject_run() etc. verify the run belongs
  to the requesting user's tenant before operating

orchestrator/state.py — add to ContinuumState:
    tenant_id: str = ""
    owner_id: str = ""      # user.id who submitted
    owner_email: str = ""   # for audit trail display

## TASK 3 — Role-based access control

Three roles:
  developer  — can submit runs, see own runs, see gate status
  reviewer   — can see all runs in tenant, approve/reject gates
  admin      — everything + see compliance reports + manage mappings

Enforce at endpoint level with a simple decorator:
    def require_role(*roles):
        def dep(user: User = Depends(_get_user)):
            if user.role not in roles:
                raise HTTPException(403, "Insufficient role")
            return user
        return Depends(dep)
    
    @app.post("/run/{run_id}/resume")
    async def resume_run(run_id: str, ..., user=require_role("reviewer","admin")):
        ...

## TASK 4 — Attributed audit trail

api/compliance.py — build_compliance_report():
Add gate approval attribution to every gate decision in the report:
    "gate_decisions": [
        {
            "gate": "story_review",
            "decision": "approved",
            "actor_email": state.audit_trail[-1]["actor_email"],
            "actor_id": state.audit_trail[-1]["actor_id"],
            "timestamp": ...,
        }
    ]

orchestrator/state.py — extend audit_trail entries:
    state.audit_trail.append({
        "t": time.time(),
        "actor": user.email,
        "actor_id": user.id,
        "tenant_id": user.tenant_id,
        "action": "gate_approved",
        "gate": gate_name,
    })

## TASK 5 — Neo4j tenant partitioning

graph_db/driver.py — all Cypher queries get tenant filter:
    # Every Episode, Spec, Component node created with tenant_id property
    MERGE (e:Episode {id: $episode_id, tenant_id: $tenant_id})
    
    # Every read query filters by tenant:
    MATCH (e:Episode {tenant_id: $tenant_id})
    WHERE e.run_id = $run_id
    RETURN e LIMIT 1

graph_db/spec_registry.py — same pattern.
Offline path (_IN_MEMORY_SPECS list): filter by tenant_id on reads.

## TASK 6 — .env.example + CLAUDE.md update

Add to .env.example:
    CONTINUUM_JWT_SECRET=dev-secret-change-in-production
    DEV_MODE=1   # enables static dev user when no JWT is supplied

Add to CLAUDE.md non-negotiable:
    # Auth is offline-safe: DEV_MODE=1 + empty token → dev user
    # Never require a real JWT in the verify suite

## VERIFY SCRIPT — scripts/verify_p03_auth.py (new, 5/5)

case A: empty token + DEV_MODE=1 → dev user returned (not error)
case B: run submitted by user A is NOT visible to user B (different tenant)
case C: developer role cannot call POST /run/{id}/resume → 403
case D: reviewer role CAN call POST /run/{id}/resume → 200
case E: compliance report includes actor_email on gate decisions

Makefile: add verify-p03 target.
Add to CLAUDE.md non-negotiable list.

## CONSTRAINTS
- DEV_MODE=1 (default) means all offline verify suites pass unchanged
- Auth is additive — existing API shape unchanged, just new middleware
- _RUNS dict key strategy: keep run_id as-is (UUID), add tenant_id
  to RunSummary and filter on list. Don't change the dict key structure
  (would break resume_run() and event_bus references).
- Neo4j tenant filter: add tenant_id property to new nodes only;
  existing nodes without tenant_id use tenant_id="legacy"

## DONE WHEN
- make verify-p03 → 5/5 PASS
- All 17+ existing checks still pass
- GET /runs returns only the requesting user's tenant's runs
- POST /run/{id}/resume → 403 for developer role
- Compliance report shows actor_email on every gate decision
- Neo4j Episode nodes include tenant_id (verified by verify_m3_learning)

Branch: feature/p03-auth-tenancy → PR → dev
```

---

## Part 5 — C3: Extensibility Sprint

### The missing integration layer

Continuum currently requires a human to open the UI and submit an intent.
A complete platform should also:
- Trigger from an ADO work item being created or moved
- Notify a reviewer via Slack/Teams when a gate needs attention
- Support multiple target repos (not just `CONTINUUM_TARGET_REPO`)

### C3 Dispatch Prompt

```
Branch: git checkout dev && git checkout -b feature/c3-extensibility

GOAL: Webhook triggers, notification system, multi-repo support.
The platform initiates work from external systems and notifies the
right people when action is needed.

## TASK 1 — Webhook trigger endpoint

api/main.py:
    @app.post("/webhooks/ado")
    async def ado_webhook(payload: Dict[str, Any], ...) -> Dict:
        """
        Receive Azure DevOps work-item webhooks.
        Triggers a new Continuum run when:
        - eventType == "workitem.created" AND workItemType in ("User Story","Task")
        - OR eventType == "workitem.updated" AND field "System.State" changed
          to "Ready for Dev"
        
        Extracts intent from work item title + description.
        Extracts business mappings from work item tags (format: "mapping:BR=Branch").
        """
        event_type = payload.get("eventType", "")
        if event_type not in ("workitem.created", "workitem.updated"):
            return {"status": "ignored"}
        
        # Extract intent
        wi = payload.get("resource", {})
        title = wi.get("fields", {}).get("System.Title", "")
        desc = wi.get("fields", {}).get("System.Description", "")
        intent = f"{title}. {desc}".strip()
        
        # Parse mappings from tags: "mapping:BR=Branch,WEB=Web"
        tags = wi.get("fields", {}).get("System.Tags", "")
        mappings = _parse_mapping_tags(tags)
        
        # Submit as a run
        run_id = str(uuid.uuid4())
        state = ContinuumState(run_id=run_id, request=intent,
                               business_mappings=mappings)
        _STORE.put(run_id, state)
        asyncio.create_task(_execute_pipeline(state, run_id))
        return {"status": "triggered", "run_id": run_id}

api/webhooks.py (new helper):
    def _parse_mapping_tags(tags: str) -> List[Dict]:
        """Parse "mapping:BR=Branch,WEB=Web" from ADO tag string."""
        ...

## TASK 2 — Notification system

integrations/notifications.py (new):
    class NotificationChannel(Protocol):
        async def send(self, message: str, run_id: str, gate: str) -> None: ...
    
    class SlackNotifier:
        """POST to CONTINUUM_SLACK_WEBHOOK_URL when set."""
        async def send(self, message, run_id, gate):
            url = os.getenv("CONTINUUM_SLACK_WEBHOOK_URL")
            if not url: return  # no-op offline
            payload = {
                "text": f"🔔 *Gate review needed* — {gate}\n"
                       f"Run: `{run_id}`\n{message}\n"
                       f"Review: {os.getenv('CONTINUUM_BASE_URL','')}/run/{run_id}"
            }
            async with aiohttp.ClientSession() as s:
                await s.post(url, json=payload)
    
    class TeamsNotifier:
        """POST to CONTINUUM_TEAMS_WEBHOOK_URL when set."""
        async def send(self, message, run_id, gate): ...
    
    def get_notifiers() -> List[NotificationChannel]:
        notifiers = []
        if os.getenv("CONTINUUM_SLACK_WEBHOOK_URL"):
            notifiers.append(SlackNotifier())
        if os.getenv("CONTINUUM_TEAMS_WEBHOOK_URL"):
            notifiers.append(TeamsNotifier())
        return notifiers  # empty list = no-op (offline safe)

api/main.py — in _mark_blocked() and human_gate_pending emitter:
    for n in get_notifiers():
        asyncio.create_task(n.send(
            f"Gate '{gate_name}' needs review for run {run_id}",
            run_id, gate_name
        ))

## TASK 3 — Multi-repo support in .pdlc/ config

orchestrator/agent_runner.py:
Currently CONTINUUM_TARGET_REPO is one env var.
Replace with per-run config loaded from the .pdlc/ directory:

    def _load_repo_config(target_repo: str) -> Dict:
        """
        Read {target_repo}/.pdlc/config.yml if it exists.
        Returns defaults if absent (backward compat).
        """
        config_path = Path(target_repo) / ".pdlc" / "config.yml"
        if not config_path.exists():
            return {"stack": "python", "test_cmd": "pytest -q", "lint_cmd": "ruff check ."}
        import yaml
        return yaml.safe_load(config_path.read_text()) or {}

Use config["test_cmd"] / config["lint_cmd"] / config["stack"] in
BoxLite.run_suite() instead of hard-coded "pytest -q" / "ruff check ."

This means: adding a new target app = add a .pdlc/config.yml. Zero code change.

.pdlc/config.yml schema (document in a new docs/PDLC_CONFIG.md):
    app_name: "Retail Banking App"
    stack: "java-spring-angular"      # or python, node, etc.
    lint_cmd: "mvn checkstyle:check"
    typecheck_cmd: null
    test_cmd: "mvn test -q"
    business_mappings:
      - {code: "BR", label: "Branch"}
      - {code: "WEB", label: "Web"}
    ado_project: "RetailBankingApp"
    ado_repo: "retail-banking-app"

## VERIFY SCRIPT — scripts/verify_c3_extensibility.py (new, 3/3)

case A: _parse_mapping_tags("mapping:BR=Branch,WEB=Web") returns correct list
case B: _load_repo_config("tests/fixtures/sample_pdlc_app") returns config dict
case C: get_notifiers() returns [] when no webhook env vars set (offline-safe)

## CONSTRAINTS
- aiohttp added to requirements for notification POSTs
  (already present for many agentic frameworks; if absent, use urllib)
- Webhook endpoint requires a shared secret header check:
  X-Continuum-Secret: env CONTINUUM_WEBHOOK_SECRET
  (skip check in DEV_MODE)
- All new integrations degrade to no-op when env vars absent

## DONE WHEN
- make verify-c3 → 3/3 PASS
- POST /webhooks/ado with a sample ADO payload creates a run
- CONTINUUM_SLACK_WEBHOOK_URL set → Slack message sent on gate_pending
- BoxLite uses config["test_cmd"] from .pdlc/config.yml when present
- All existing verify suites pass

Branch: feature/c3-extensibility → PR → dev
```

---

## Part 6 — C4: Polish Sprint

### C4 Dispatch Prompt

```
Branch: git checkout dev && git checkout -b feature/c4-polish

GOAL: React 19, Langfuse observability, API cleanup.
One session, quality-of-life improvements.

## TASK 1 — React 19 upgrade

ui/package.json:
  "react": "^19.0.0",
  "react-dom": "^19.0.0"

React 19 changes that affect this codebase:
- useEffect cleanup: no-op (we already clean up EventSource)
- StrictMode double-invoke: useSSE already handles this (closes + reopens)
- ref as prop: ArtifactViewer may need ref forwarding update if using forwardRef
  (check and fix if needed)
- No react-dom/client createRoot change needed (already using React 18 createRoot)
Run: cd ui && npm install && npm run build — fix any TypeScript errors.

## TASK 2 — Self-hosted Langfuse integration (opt-in)

integrations/langfuse_tracer.py (new):
    """
    Optional Langfuse tracing. Activated when LANGFUSE_SECRET_KEY +
    LANGFUSE_PUBLIC_KEY + LANGFUSE_HOST are set. No-op otherwise.
    Uses the langfuse Python SDK.
    """
    def trace_run(run_id: str, request: str, tenant_id: str):
        if not _langfuse(): return
        return _langfuse().trace(
            name="continuum_run",
            id=run_id,
            input=request,
            metadata={"tenant_id": tenant_id},
        )
    
    def score_run(run_id: str, evidence_layers: list):
        if not _langfuse(): return
        score = sum(1 for l in evidence_layers if l["status"] == "pass") / len(evidence_layers)
        _langfuse().score(trace_id=run_id, name="evidence_stack", value=score)

orchestrator/agent_runner.py: call trace_run() at run start, score_run() at done.
All calls wrapped in try/except — Langfuse errors never break a run.
Add langfuse>=2.0 to requirements (conditional: only imported when keys set).

## TASK 3 — API router consolidation

api/main.py currently has some duplicate route patterns flagged in the PR.
Audit and remove:
- Any duplicate @app.get / @app.post registrations on the same path
- Any routes that are stubs (just return {"status": "not implemented"})
- Consolidate the /run/{id}/* namespace: group resume, reject,
  escalate-resolve, evidence, compliance-report under a clear pattern.

Create api/router/runs.py with an APIRouter for all /run/* paths.
Create api/router/specs.py for /specs/* paths.
Create api/router/webhooks.py for /webhooks/* paths (from C3).
Mount in main.py: app.include_router(runs_router, prefix="")

## TASK 4 — Langfuse dashboard setup docs

docs/LANGFUSE_SETUP.md (new):
  - docker compose snippet for self-hosted Langfuse (Canada-resident)
  - Required env vars
  - How to view Continuum traces: filter by metadata.tenant_id
  - How to set up the Evidence Stack score as a Langfuse score

## CONSTRAINTS
- React 19 upgrade must not break npm run build
- Langfuse is strictly opt-in — zero behaviour change when keys absent
- API router split is non-breaking — all paths identical, just reorganised
- langfuse package added to requirements.txt with comment # optional

## DONE WHEN
- cd ui && npm run build → clean build (no TypeScript errors)
- React 19 in package.json
- LANGFUSE_SECRET_KEY set → traces appear in Langfuse dashboard
- LANGFUSE_SECRET_KEY unset → zero Langfuse imports, zero errors
- No duplicate route registrations in the API
- All existing verify suites pass

Branch: feature/c4-polish → PR → dev
```

---

## Part 7 — Complete Solution Map

### What "complete" looks like after all sprints

```
CONTINUUM COMPLETE SOLUTION
════════════════════════════

ENTRY POINTS (C3)
  • Human submits intent via Work Queue UI
  • ADO webhook triggers from work item creation
  • API POST /run (programmatic)

IDENTITY + TENANCY (P0.3)
  • Every request carries a JWT identity
  • Every run is attributed to a user + tenant
  • Reviewers notified via Slack/Teams (C3)
  • RBAC: developer / reviewer / admin

PIPELINE (M0-M13, Box Lite P0.2)
  Intent → BSA → Gate 1 → Architect → Gate 2 → Planner
  → DB → Backend → Frontend (each in BoxLite)
  → Gate 3 (scope-guard, lint, test, sast)
  → Security → Merge Gate → SIT → Verify → Quality Gate → UAT

SPEC REGISTRY (M11)
  Per-component, versioned, drift-detecting.
  Run N+1 grounds on Run N's approved spec.

EVIDENCE + COMPLIANCE (P1.1, M12)
  6 truly independent signals (after P1.1 split).
  Compliance report packages everything for auditors.
  EU AI Act / OSFI ready.

OBSERVABILITY (C2)
  Live agent activity stream (3 tiers)
  TraceTimeline of agent handoffs
  Token streaming (opt-in)
  Langfuse traces (opt-in, self-hosted, Canada)
  OTel → App Insights (opt-in)

MEMORY + LEARNING (M3, M5, M10)
  Neo4j episodic memory (per-tenant after P0.3)
  Evolution Agent proposes harness improvements
  human_promote() is the only mutation path
  ASSERT + Rubric eval suite

EXTENSIBILITY (C3, M8)
  D33 two-layer split: platform repo + .pdlc/ per app
  New app onboarded via config.yml, zero code change
  Webhook trigger from ADO
  Multi-stack support (Java/Python/Node/Angular)

UPGRADE PATH
  Box Lite → ACA Sessions → Hyperlight (M14)
  LangGraph stays as orchestrator
  MAF harness for agents (M14 graduation)
```

### The sessions map

| Sprint | Sessions | What it unlocks |
|---|---|---|
| C1 Correctness | 1 | Reliable at scale, SSE works on reconnect |
| C2 Observability | 2 | Demo quality, "alive" feeling |
| P0.3 Auth/Tenancy | 3 | Any real client can use it |
| C3 Extensibility | 2 | Webhook triggers, notifications, multi-app |
| C4 Polish | 1 | React 19, Langfuse, clean API |
| **Total** | **9 sessions** | **Production-grade complete solution** |

### Dispatch sequence

```
NOW:     C1 (1 session) — correctness, unblocks everything
THEN:    C2 + P0.3 in parallel (C2: UI, P0.3: backend)
THEN:    C3 (builds on P0.3 for auth on webhooks)
THEN:    C4 (polish, can happen any time)
HORIZON: M14 MAF graduation (Hyperlight, when GA)
```

---

## Part 8 — What Makes This a Complete Enterprise Solution

Three things distinguish a working POC from an enterprise solution:

**1. Trust chain is end-to-end.** Every claim is literally verifiable.
The code ran (BoxLite). The test passed (real pytest stdout). The human
approved (attributed identity, P0.3). The spec was followed (M7).
The audit trail is tamper-evident (append-only Postgres). An auditor
can trace a production change back to the original intent, the human
who approved each gate, the test results, and the evidence stack.

**2. The system knows itself.** The Evolution Agent (M5) observes failure
patterns and proposes harness improvements. The Spec Registry (M11) detects
when a new run drifts from past agreed specs. The compliance report (M12)
is always one API call away. Langfuse (C4) shows exactly where time and
money are being spent. This is self-aware infrastructure.

**3. Humans are in the right place.** Not everywhere (wastes time), not
nowhere (unsafe). Gates 1–4 are where human judgment is genuinely
needed — plan, architecture, release, incident. Everything else is
automated and independently verified. The gate-removal ladder (§7 of
the sibling doc) provides a principled path to removing gates as
trust is earned and measured. This is what "earned and revocable
autonomy" means in practice.

No other tool in the market currently combines:
- Spec-Driven Development (specs execute as validation gates)
- Artifact-centric state machine (15 states, policy engine)
- Per-agent isolated execution (Box Lite / ACA)
- 6-layer independent evidence stack
- Episodic memory across runs
- Self-improving harness (governed by human_promote())
- Full compliance packaging (EU AI Act / OSFI ready)

That combination is Continuum's moat.

---

*Continuum Complete Solution Plan · June 2026*
*github.com/KIRTIRAJ4327/continuum · dev branch*
