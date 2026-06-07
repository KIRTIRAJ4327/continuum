"""FastAPI backend for Continuum — M2 edition.

New in M2:
  • POST /run        — starts pipeline in background; returns run_id immediately
  • GET  /events/{run_id} — SSE stream of agent events (text/event-stream)
  • GET  /artifacts/{run_id}/{agent} — agent artifact (story/contract/code/…)
  • GET  /runs       — recent runs list with status

The M0 offline / verify path is preserved:
  • _execute_pipeline(state) is still an awaitable called by verify_m0_loop.py.
  • 11/11 and 3/3 still pass with no external dependencies.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from orchestrator.agent_runner import AgentContext, run_agent
from orchestrator.events import event_bus
from orchestrator.state import AgentRole, ContinuumState

logger = logging.getLogger(__name__)

app = FastAPI(title="Continuum", version="0.2.0")

# Allow the Vite dev server (port 5173) to call the API during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory run store. LangGraph PostgresCheckpointer replaces this in production.
_RUNS: Dict[str, ContinuumState] = {}
_MAX_RETRIES = 3


# --------------------------------------------------------------------------- #
# Request / response models
# --------------------------------------------------------------------------- #
class FeatureRequest(BaseModel):
    request: str


def _state_to_dict(state: ContinuumState) -> Dict[str, Any]:
    """Serialise state for JSON responses."""
    return {
        "run_id": state.run_id,
        "request": state.request,
        "current_agent": state.current_agent.value if state.current_agent else None,
        "story": state.story,
        "contract": state.contract,
        "schema": state.schema,
        "dag": state.dag,
        "code_files": list((state.code or {}).keys()),
        "gates": [
            {
                "name": g.name,
                "status": g.status,
                "retry_count": g.retry_count,
                "error": (g.error_message or "")[:300],
            }
            for g in state.gates
        ],
        "human_approval_pending": state.human_approval_pending,
        "approval_gate_name": state.approval_gate_name,
        "story_approved": state.story_approved,
        "design_approved": state.design_approved,
        "merge_approved": state.merge_approved,
        "error_message": state.error_message,
        "messages": len(state.messages),
        "started_at": state.started_at,
        "completed_at": state.completed_at,
    }


def _gate(state: ContinuumState, name: str) -> Optional[Any]:
    return next((g for g in state.gates if g.name == name), None)


def _classify(state: ContinuumState) -> str:
    if state.human_approval_pending:
        return "awaiting_approval"
    if state.error_message:
        return "failed"
    if state.completed_at:
        return "complete"
    return "running"


# --------------------------------------------------------------------------- #
# Core pipeline — kept as a plain awaitable so verify_m0_loop.py still works
# --------------------------------------------------------------------------- #
async def _execute_pipeline(state: ContinuumState, run_id: str = "") -> None:
    """
    Walk the pipeline applying retry/escalate rules.

    run_id (optional): when set, events are emitted to event_bus so the SSE
    stream and React UI receive live updates.  When absent (offline tests) the
    function runs silently with no external side-effects.
    """
    ctx = AgentContext.from_env()
    ctx.run_id = run_id  # wire the run_id so run_agent() can emit events

    async def _run(role: str) -> None:
        await run_agent(state, role, ctx)
        # Emit human-gate-pending if the run paused for approval
        if run_id and state.human_approval_pending:
            await event_bus.emit(run_id, {
                "event_type": "human_gate_pending",
                "agent": role,
                "run_id": run_id,
                "data": {"gate_name": state.approval_gate_name},
            })

    # 1. BSA → Architect → Planner (no retry gates on this stretch in M0)
    for role in ("bsa", "architect", "planner"):
        await _run(role)
        if state.human_approval_pending:
            return  # pause here; resume() will restart us

    # 2. Developer with local_verify retry loop
    for attempt in range(_MAX_RETRIES + 1):
        await _run("developer")
        gate = _gate(state, "local_verify")
        if gate is None or gate.status == "green":
            break
        if gate.retry_count >= _MAX_RETRIES:
            state.human_approval_pending = True
            state.approval_gate_name = "local_verify"
            if run_id:
                await event_bus.emit(run_id, {
                    "event_type": "human_gate_pending",
                    "agent": "developer",
                    "run_id": run_id,
                    "data": {"gate_name": "local_verify", "reason": "max_retries_exceeded"},
                })
            logger.error("local_verify failed after %d retries — escalating", _MAX_RETRIES)
            return
        gate.retry_count += 1
        if run_id:
            await event_bus.emit(run_id, {
                "event_type": "gate_retry",
                "agent": "developer",
                "run_id": run_id,
                "data": {"gate_name": "local_verify", "attempt": gate.retry_count},
            })
        logger.warning("local_verify red, retry %d/%d", gate.retry_count, _MAX_RETRIES)

    # 3. Security (only when local_verify is green)
    lv = _gate(state, "local_verify")
    if lv is None or lv.status == "green":
        await _run("security")

    state.completed_at = time.time()

    if run_id:
        await event_bus.emit(run_id, {
            "event_type": "run_complete",
            "agent": "",
            "run_id": run_id,
            "data": {"status": _classify(state)},
        })


# --------------------------------------------------------------------------- #
# API routes
# --------------------------------------------------------------------------- #
@app.post("/run")
async def start_run(req: FeatureRequest) -> Dict[str, Any]:
    """
    Submit a feature request.  Returns run_id immediately; pipeline runs in
    background.  Connect to GET /events/{run_id} for live progress.
    """
    if not req.request.strip():
        raise HTTPException(status_code=400, detail="request is empty")

    run_id = uuid.uuid4().hex[:12]
    state = ContinuumState(request=req.request, run_id=run_id, started_at=time.time())
    _RUNS[run_id] = state

    # Background task — emits events as each agent completes.
    async def _bg() -> None:
        try:
            await _execute_pipeline(state, run_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Pipeline failed for run %s", run_id)
            state.error_message = str(exc)
            state.completed_at = time.time()
            await event_bus.emit(run_id, {
                "event_type": "run_complete",
                "agent": "",
                "run_id": run_id,
                "data": {"status": "failed", "error": str(exc)},
            })

    asyncio.create_task(_bg())

    return {"run_id": run_id, "status": "running"}


@app.get("/runs")
async def list_runs() -> List[Dict[str, Any]]:
    """Return all recorded runs, newest first."""
    runs = sorted(_RUNS.values(), key=lambda s: s.started_at or 0, reverse=True)
    return [
        {
            "run_id": s.run_id,
            "request": (s.request or "")[:80],
            "status": _classify(s),
            "started_at": s.started_at,
            "completed_at": s.completed_at,
            "current_agent": s.current_agent.value if s.current_agent else None,
        }
        for s in runs
    ]


@app.get("/run/{run_id}")
async def get_run(run_id: str) -> Dict[str, Any]:
    state = _RUNS.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found")
    return {"run_id": run_id, "status": _classify(state), "state": _state_to_dict(state)}


@app.get("/events/{run_id}")
async def stream_events(run_id: str) -> StreamingResponse:
    """
    Server-Sent Events stream for a run.  Replays historical events first
    (so a late-joining client does not miss agent_start / agent_complete
    events that already fired), then streams live events until run_complete.
    """
    if run_id not in _RUNS:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found")

    async def _generator():
        q, history = event_bus.subscribe(run_id)
        try:
            # Replay history so the UI catches up immediately on first connect.
            for ev in history:
                yield f"data: {json.dumps(ev, default=str)}\n\n"

            # Stream live events.
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=25.0)
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
                    if ev.get("event_type") == "run_complete":
                        break
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"  # SSE comment keeps the connection alive
        except asyncio.CancelledError:
            pass
        finally:
            event_bus.unsubscribe(run_id, q)

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable Nginx buffering for SSE
        },
    )


@app.get("/artifacts/{run_id}/{agent}")
async def get_artifact(run_id: str, agent: str) -> Dict[str, Any]:
    """Return the primary artifact produced by an agent for a given run."""
    state = _RUNS.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found")

    artifacts: Dict[str, Any] = {}
    role = agent.lower()
    if role == AgentRole.BSA.value:
        artifacts = {"story": state.story}
    elif role == AgentRole.ARCHITECT.value:
        artifacts = {
            "contract": state.contract,
            "schema": state.schema,
            "dag": state.dag,
        }
    elif role == AgentRole.PLANNER.value:
        artifacts = {"plan": (state.dag or {}).get("plan")}
    elif role in (AgentRole.DEVELOPER.value, "database", "backend", "frontend"):
        artifacts = {"code": state.code or {}}
    elif role == AgentRole.SECURITY.value:
        sec_gate = _gate(state, "security_sast")
        artifacts = {
            "gate_status": sec_gate.status if sec_gate else "unknown",
            "error": sec_gate.error_message if sec_gate else None,
        }
    else:
        raise HTTPException(status_code=404, detail=f"unknown agent '{agent}'")

    return {"run_id": run_id, "agent": agent, "artifacts": artifacts}


@app.post("/run/{run_id}/resume")
async def resume_run(run_id: str, approved: bool = True) -> Dict[str, Any]:
    """Resume a run that is paused at a human approval gate."""
    state = _RUNS.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found")
    if not state.human_approval_pending:
        raise HTTPException(status_code=400, detail="run is not awaiting human approval")

    gate_name = state.approval_gate_name or ""

    if approved:
        # Set the appropriate approval flag.
        if gate_name == "story_review":
            state.story_approved = True
        elif gate_name == "design_review":
            state.design_approved = True
        elif gate_name == "merge_review":
            state.merge_approved = True
        else:
            # Local verify / other gate failure — clear retry count so it re-runs
            g = _gate(state, gate_name)
            if g:
                g.retry_count = 0
                g.status = "pending"
    # else: rejected — pipeline stays paused with human_approval_pending=True

    state.human_approval_pending = False
    state.approval_gate_name = None

    await event_bus.emit(run_id, {
        "event_type": "human_gate_resolved",
        "agent": "",
        "run_id": run_id,
        "data": {"gate_name": gate_name, "approved": approved},
    })

    if approved:
        # Resume the pipeline in a background task from where it left off.
        async def _resume_bg() -> None:
            try:
                await _execute_pipeline(state, run_id)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Resumed pipeline failed for run %s", run_id)
                state.error_message = str(exc)
                state.completed_at = time.time()

        asyncio.create_task(_resume_bg())

    return {"run_id": run_id, "approved": approved, "status": _classify(state)}


@app.post("/run/{run_id}/resume")
async def resume_run(run_id: str, approved: bool = True) -> Dict[str, Any]:
    """
    Resume a run that is suspended at a human_gate interrupt().

    This endpoint is called by an operator after reviewing the artifacts surfaced
    by GET /run/{run_id}. Pass ?approved=true to unblock the gate and continue,
    or ?approved=false to reject (leaves the run in 'escalated' state).

    For M0/offline runs (no LangGraph checkpointer) the state is mutated
    directly in-memory so that the response reflects the decision.
    """
    state = _RUNS.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found")
    if not state.human_approval_pending:
        raise HTTPException(status_code=409, detail="run is not waiting for human approval")

    gate_name = state.approval_gate_name or ""

    if not approved:
        # Rejection — leave the run escalated; no further processing
        logger.info("[RESUME] Run %s gate '%s' rejected by operator", run_id, gate_name)
        return {"run_id": run_id, "status": "escalated", "state": _state_to_dict(state)}

    # Approval — update the relevant flag and continue the pipeline
    logger.info("[RESUME] Run %s gate '%s' approved by operator", run_id, gate_name)
    if gate_name == "story_review":
        state.story_approved = True
    elif gate_name == "design_review":
        state.design_approved = True
    elif gate_name == "merge_review":
        state.merge_approved = True
    else:
        # Gate-failure retry (e.g., local_verify escalated after 3 fails)
        gate = _gate(state, gate_name)
        if gate:
            gate.retry_count = 0
            gate.status = "pending"

    state.human_approval_pending = False
    state.approval_gate_name = None

    # Re-run the remaining pipeline stages
    try:
        ctx = AgentContext.from_env()

        if gate_name == "story_review":
            # Story approved — run the rest of the pipeline from Architect onwards
            for role in ("architect", "planner"):
                await run_agent(state, role, ctx)
            for _ in range(_MAX_RETRIES + 1):
                await run_agent(state, "developer", ctx)
                gv = _gate(state, "local_verify")
                if gv is None or gv.status == "green":
                    break
                if gv.retry_count >= _MAX_RETRIES:
                    state.human_approval_pending = True
                    state.approval_gate_name = "local_verify"
                    break
                gv.retry_count += 1
            if not state.human_approval_pending:
                lv = _gate(state, "local_verify")
                if lv and lv.status == "green":
                    await run_agent(state, "security", ctx)
                    state.human_approval_pending = True
                    state.approval_gate_name = "merge_review"

        elif gate_name == "design_review":
            # Design approved — run Planner → Developer → Security
            await run_agent(state, "planner", ctx)
            for _ in range(_MAX_RETRIES + 1):
                await run_agent(state, "developer", ctx)
                gv = _gate(state, "local_verify")
                if gv is None or gv.status == "green":
                    break
                if gv.retry_count >= _MAX_RETRIES:
                    state.human_approval_pending = True
                    state.approval_gate_name = "local_verify"
                    break
                gv.retry_count += 1
            if not state.human_approval_pending:
                lv = _gate(state, "local_verify")
                if lv and lv.status == "green":
                    await run_agent(state, "security", ctx)
                    state.human_approval_pending = True
                    state.approval_gate_name = "merge_review"

        elif gate_name == "merge_review":
            # Merge approved — finalise run
            state.completed_at = time.time()

        else:
            # Gate-failure retry — re-run developer
            for _ in range(_MAX_RETRIES + 1):
                await run_agent(state, "developer", ctx)
                gv = _gate(state, gate_name)
                if gv is None or gv.status == "green":
                    break
                if gv.retry_count >= _MAX_RETRIES:
                    state.human_approval_pending = True
                    state.approval_gate_name = gate_name
                    break
                gv.retry_count += 1
            if not state.human_approval_pending:
                lv = _gate(state, "local_verify")
                if lv and lv.status == "green":
                    await run_agent(state, "security", ctx)

        if not state.completed_at and not state.human_approval_pending:
            state.completed_at = time.time()

    except Exception as exc:  # noqa: BLE001
        logger.exception("Resume pipeline failed for run %s", run_id)
        state.error_message = str(exc)

    return {"run_id": run_id, "status": _classify(state), "state": _state_to_dict(state)}


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}


# --------------------------------------------------------------------------- #
# Serve built React app (production only — Vite dev server used during dev)
# --------------------------------------------------------------------------- #
_UI_DIST = Path(__file__).resolve().parent.parent / "ui" / "dist"
if _UI_DIST.is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=str(_UI_DIST), html=True), name="ui")
    logger.info("Serving UI from %s", _UI_DIST)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
