"""FastAPI backend for Continuum — M6 edition.

New in M6:
  • run_status field (running/waiting_gate/blocked/returned/done/failed)
  • cost_usd accumulated per agent run
  • POST /run/{id}/reject          — human rejects at a gate → run_returned
  • POST /run/{id}/escalate-resolve — unblock after retry exhaustion
  • GET  /runs/{id}/evidence        — 6-layer evidence stack
  • GET  /runs extended with run_status, cost_usd, duration_s, stage_idx

M0-M5 offline / verify path is preserved:
  • _execute_pipeline(state) is still a plain awaitable (no run_id required).
  • 11/11 + 3/3 + 6/6 (M3/M5) still pass with no external dependencies.
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

app = FastAPI(title="Continuum", version="0.6.0")


@app.on_event("startup")
async def validate_env() -> None:
    try:
        from config import warn_missing_optional, AZURE_AI_LIVE, AZURE_ADO_LIVE  # type: ignore
        missing = warn_missing_optional()
        if not missing:
            logger.info(
                "[startup] All optional env vars set — live path active "
                "(Azure AI: %s, ADO: %s)",
                AZURE_AI_LIVE,
                AZURE_ADO_LIVE,
            )
        else:
            logger.info(
                "[startup] Running in offline/stub mode for: %s "
                "— set vars in .env to enable live integrations",
                ", ".join(missing),
            )
    except ImportError:
        logger.debug("[startup] config module not available — skipping env validation")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory run store. LangGraph PostgresCheckpointer replaces this in production.
_RUNS: Dict[str, ContinuumState] = {}
_MAX_RETRIES = 3

# Canonical stage order for stage_idx computation.
_STAGE_ORDER = [
    "bsa", "architect", "planner",
    "database", "backend", "frontend",
    "security", "memory",
]


# --------------------------------------------------------------------------- #
# Request / response models
# --------------------------------------------------------------------------- #
class FeatureRequest(BaseModel):
    request: str
    business_mappings: List[Dict[str, Any]] = []  # M7: optional mapping table


class RejectRequest(BaseModel):
    gate: str   # "story_review" | "design_review" | "local_verify" | …
    reason: str


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
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
        "episodes": len(state.episodes or []),
        "episodes_written": len(state.episodes_written or []),
        "started_at": state.started_at,
        "completed_at": state.completed_at,
        # M6 fields
        "run_status": state.run_status,
        "cost_usd": round(state.cost_usd, 4),
        "reject_reason": state.reject_reason,
    }


def _gate(state: ContinuumState, name: str) -> Optional[Any]:
    return next((g for g in state.gates if g.name == name), None)


def _classify(state: ContinuumState) -> str:
    """Return the run status string (M6: uses run_status field)."""
    return state.run_status


def _duration_s(state: ContinuumState) -> Optional[float]:
    if state.started_at is None:
        return None
    end = state.completed_at or time.time()
    return round(end - state.started_at, 1)


def _stage_idx(state: ContinuumState) -> int:
    if state.current_agent is None:
        return -1
    try:
        return _STAGE_ORDER.index(state.current_agent.value)
    except ValueError:
        return -1


# --------------------------------------------------------------------------- #
# Core pipeline — kept as a plain awaitable so verify_m0_loop.py still works
# --------------------------------------------------------------------------- #
async def _execute_pipeline(state: ContinuumState, run_id: str = "") -> None:
    """
    Walk the pipeline applying retry/escalate rules.

    run_id (optional): when set, events are emitted to event_bus so the SSE
    stream and React UI receive live updates. When absent (offline tests) the
    function runs silently with no external side-effects.
    """
    state.run_status = "running"
    ctx = AgentContext.from_env()
    ctx.run_id = run_id

    async def _run(role: str) -> None:
        await run_agent(state, role, ctx)
        if run_id and state.human_approval_pending:
            await event_bus.emit(run_id, {
                "event_type": "human_gate_pending",
                "agent": role,
                "run_id": run_id,
                "data": {"gate_name": state.approval_gate_name},
            })

    # 1. BSA → Architect → Planner
    for role in ("bsa", "architect", "planner"):
        await _run(role)
        if state.human_approval_pending:
            state.run_status = "waiting_gate"
            return  # pause; resume() will restart from the right stage

    # 2. Developer with local_verify retry loop
    for attempt in range(_MAX_RETRIES + 1):
        await _run("developer")
        gate = _gate(state, "local_verify")
        if gate is None or gate.status == "green":
            break
        if gate.retry_count >= _MAX_RETRIES:
            state.run_status = "blocked"
            state.human_approval_pending = True
            state.approval_gate_name = "local_verify"
            if run_id:
                await event_bus.emit(run_id, {
                    "event_type": "run_blocked",
                    "agent": "developer",
                    "run_id": run_id,
                    "data": {
                        "gate_name": "local_verify",
                        "error_message": (gate.error_message or "")[:500],
                    },
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

    # 4. M3: Memory agent (non-fatal)
    try:
        await _run("memory")
    except Exception as mem_exc:  # noqa: BLE001
        logger.warning("Memory agent failed (non-fatal): %s", mem_exc)

    state.completed_at = time.time()
    state.run_status = "done"

    if run_id:
        await event_bus.emit(run_id, {
            "event_type": "run_complete",
            "agent": "",
            "run_id": run_id,
            "data": {
                "status": "done",
                "episodes_written": len(state.episodes_written or []),
                "cost_usd": state.cost_usd,
            },
        })


# --------------------------------------------------------------------------- #
# Resume helper — runs remaining stages after a gate approval
# --------------------------------------------------------------------------- #
async def _resume_pipeline(state: ContinuumState, run_id: str, gate_name: str) -> None:
    """Continue the pipeline from where it was paused, after a gate approval."""
    ctx = AgentContext.from_env()
    ctx.run_id = run_id
    state.run_status = "running"

    async def _run(role: str) -> None:
        await run_agent(state, role, ctx)

    async def _developer_loop(resume_gate: str) -> None:
        """Run developer with retry, then security + memory on success."""
        for _ in range(_MAX_RETRIES + 1):
            await _run("developer")
            gv = _gate(state, resume_gate)
            if gv is None or gv.status == "green":
                break
            if gv.retry_count >= _MAX_RETRIES:
                state.run_status = "blocked"
                state.human_approval_pending = True
                state.approval_gate_name = resume_gate
                if run_id:
                    await event_bus.emit(run_id, {
                        "event_type": "run_blocked",
                        "agent": "developer",
                        "run_id": run_id,
                        "data": {
                            "gate_name": resume_gate,
                            "error_message": (gv.error_message or "")[:500],
                        },
                    })
                return
            gv.retry_count += 1
            if run_id:
                await event_bus.emit(run_id, {
                    "event_type": "gate_retry",
                    "agent": "developer",
                    "run_id": run_id,
                    "data": {"gate_name": resume_gate, "attempt": gv.retry_count},
                })

        if state.human_approval_pending:
            return

        lv = _gate(state, "local_verify")
        if lv is None or lv.status == "green":
            await _run("security")

        try:
            await _run("memory")
        except Exception as mem_exc:  # noqa: BLE001
            logger.warning("Memory agent failed (non-fatal): %s", mem_exc)

        state.completed_at = time.time()
        state.run_status = "done"
        if run_id:
            await event_bus.emit(run_id, {
                "event_type": "run_complete",
                "agent": "",
                "run_id": run_id,
                "data": {"status": "done", "cost_usd": state.cost_usd},
            })

    if gate_name == "story_review":
        await _run("architect")
        await _run("planner")
        await _developer_loop("local_verify")

    elif gate_name == "design_review":
        await _run("planner")
        await _developer_loop("local_verify")

    elif gate_name == "merge_review":
        state.completed_at = time.time()
        state.run_status = "done"
        if run_id:
            await event_bus.emit(run_id, {
                "event_type": "run_complete",
                "agent": "",
                "run_id": run_id,
                "data": {"status": "done", "cost_usd": state.cost_usd},
            })

    else:
        # local_verify failure retry — resume developer chain
        await _developer_loop(gate_name)


# --------------------------------------------------------------------------- #
# API routes
# --------------------------------------------------------------------------- #
@app.post("/run")
async def start_run(req: FeatureRequest) -> Dict[str, Any]:
    """Submit a feature request. Returns run_id immediately; pipeline runs in background."""
    if not req.request.strip():
        raise HTTPException(status_code=400, detail="request is empty")

    run_id = uuid.uuid4().hex[:12]
    state = ContinuumState(
        request=req.request,
        run_id=run_id,
        started_at=time.time(),
        business_mappings=req.business_mappings,
    )
    _RUNS[run_id] = state

    async def _bg() -> None:
        try:
            await _execute_pipeline(state, run_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Pipeline failed for run %s", run_id)
            state.error_message = str(exc)
            state.run_status = "failed"
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
            "run_status": s.run_status,
            "started_at": s.started_at,
            "completed_at": s.completed_at,
            "current_agent": s.current_agent.value if s.current_agent else None,
            # M6 additions
            "cost_usd": round(s.cost_usd, 4),
            "duration_s": _duration_s(s),
            "stage_idx": _stage_idx(s),
            "reject_reason": s.reject_reason,
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
    """SSE stream for a run. Replays history on connect then streams live events."""
    if run_id not in _RUNS:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found")

    async def _generator():
        q, history = event_bus.subscribe(run_id)
        try:
            for ev in history:
                yield f"data: {json.dumps(ev, default=str)}\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=25.0)
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
                    if ev.get("event_type") == "run_complete":
                        break
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            event_bus.unsubscribe(run_id, q)

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
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
        artifacts = {"contract": state.contract, "schema": state.schema, "dag": state.dag}
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
    elif role == AgentRole.MEMORY.value:
        artifacts = {
            "episodes_retrieved": state.episodes or [],
            "episodes_written": state.episodes_written or [],
        }
    else:
        raise HTTPException(status_code=404, detail=f"unknown agent '{agent}'")

    return {"run_id": run_id, "agent": agent, "artifacts": artifacts}


@app.post("/run/{run_id}/resume")
async def resume_run(run_id: str, approved: bool = True) -> Dict[str, Any]:
    """Resume a run paused at a human approval gate."""
    state = _RUNS.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found")
    if not state.human_approval_pending:
        raise HTTPException(status_code=409, detail="run is not waiting for human approval")

    gate_name = state.approval_gate_name or ""

    if not approved:
        logger.info("[RESUME] Run %s gate '%s' rejected by operator", run_id, gate_name)
        return {"run_id": run_id, "status": "escalated", "state": _state_to_dict(state)}

    logger.info("[RESUME] Run %s gate '%s' approved by operator", run_id, gate_name)
    if gate_name == "story_review":
        state.story_approved = True
    elif gate_name == "design_review":
        state.design_approved = True
    elif gate_name == "merge_review":
        state.merge_approved = True
    else:
        g = _gate(state, gate_name)
        if g:
            g.retry_count = 0
            g.status = "pending"

    state.human_approval_pending = False
    state.approval_gate_name = None

    await event_bus.emit(run_id, {
        "event_type": "human_gate_resolved",
        "agent": "",
        "run_id": run_id,
        "data": {"gate_name": gate_name, "approved": True},
    })

    async def _resume_bg() -> None:
        try:
            await _resume_pipeline(state, run_id, gate_name)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Resumed pipeline failed for run %s", run_id)
            state.error_message = str(exc)
            state.run_status = "failed"

    asyncio.create_task(_resume_bg())
    return {"run_id": run_id, "approved": True, "status": _classify(state)}


@app.post("/run/{run_id}/reject")
async def reject_run(run_id: str, req: RejectRequest) -> Dict[str, Any]:
    """Reject at a gate — sets run_status='returned', emits run_returned."""
    state = _RUNS.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found")

    state.run_status = "returned"
    state.reject_reason = req.reason
    state.human_approval_pending = False
    state.approval_gate_name = None

    await event_bus.emit(run_id, {
        "event_type": "run_returned",
        "agent": "",
        "run_id": run_id,
        "data": {"gate": req.gate, "reason": req.reason},
    })

    logger.info("[REJECT] Run %s gate '%s' returned: %s", run_id, req.gate, req.reason)
    return {"run_id": run_id, "status": "returned"}


@app.post("/run/{run_id}/escalate-resolve")
async def escalate_resolve(run_id: str) -> Dict[str, Any]:
    """Reset a blocked run and re-drive the developer chain."""
    state = _RUNS.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found")

    gate_name = state.approval_gate_name or "local_verify"
    g = _gate(state, gate_name)
    if g:
        g.retry_count = 0
        g.status = "pending"

    state.human_approval_pending = False
    state.approval_gate_name = None

    async def _esc_bg() -> None:
        try:
            await _resume_pipeline(state, run_id, gate_name)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Escalate-resolve failed for run %s", run_id)
            state.error_message = str(exc)
            state.run_status = "failed"

    asyncio.create_task(_esc_bg())
    return {"run_id": run_id, "status": "running"}


@app.get("/runs/{run_id}/evidence")
async def get_evidence(run_id: str) -> List[Dict[str, Any]]:
    """Return the 6-layer evidence stack for a run."""
    state = _RUNS.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found")
    try:
        from evals.evidence_stack import build_evidence_stack  # lazy — evals optional
        return build_evidence_stack(state)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"evidence_stack error: {exc}")


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
