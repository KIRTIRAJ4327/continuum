"""FastAPI backend for Continuum.

In M0 we don't require LangGraph to serve the API — the agent execution core
(`orchestrator.agent_runner`) already runs the full BSA→Architect→Planner→
Developer→Security pipeline. This endpoint walks those agents directly and
honours the same gate-retry rules the LangGraph orchestrator will use, so a
local `make run` works without Postgres/LangGraph stacks installed.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from orchestrator.agent_runner import AgentContext, run_agent
from orchestrator.state import AgentRole, ContinuumState

logger = logging.getLogger(__name__)

app = FastAPI(title="Continuum", version="0.1.0")

# In-memory run store. LangGraph's PostgresCheckpointer will replace this when
# the orchestrator graph runs in front of the API.
_RUNS: Dict[str, ContinuumState] = {}
_MAX_RETRIES = 3


class FeatureRequest(BaseModel):
    request: str


def _state_to_dict(state: ContinuumState) -> Dict[str, Any]:
    """Serialise state for JSON responses (gates/messages get compact form)."""
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
        "error_message": state.error_message,
        "messages": len(state.messages),
        "started_at": state.started_at,
        "completed_at": state.completed_at,
    }


def _gate(state: ContinuumState, name: str) -> Optional[Any]:
    return next((g for g in state.gates if g.name == name), None)


async def _execute_pipeline(state: ContinuumState) -> None:
    """
    Walk the M0 pipeline applying the same retry/escalate rules as graph.py's
    `_route()`. Terminates by gate-green OR human-escalation (Rule 4).
    """
    ctx = AgentContext.from_env()

    # 1. BSA → Architect → Planner (no gates that block these in M0)
    for role in ("bsa", "architect", "planner"):
        await run_agent(state, role, ctx)

    # 2. Developer with the local_verify retry loop.
    for _ in range(_MAX_RETRIES + 1):
        await run_agent(state, "developer", ctx)
        gate = _gate(state, "local_verify")
        if gate is None or gate.status == "green":
            break
        if gate.retry_count >= _MAX_RETRIES:
            state.human_approval_pending = True
            state.approval_gate_name = "local_verify"
            logger.error("local_verify failed after %d retries — escalating", _MAX_RETRIES)
            break
        gate.retry_count += 1
        logger.warning("local_verify red, retry %d/%d", gate.retry_count, _MAX_RETRIES)

    # 3. Security only runs if local_verify is green (Rule 2: every stage gated).
    lv = _gate(state, "local_verify")
    if lv is not None and lv.status == "green":
        await run_agent(state, "security", ctx)

    state.completed_at = time.time()


@app.post("/run")
async def run_orchestrator(req: FeatureRequest) -> Dict[str, Any]:
    """Submit a feature request, run the orchestrator, return the run id + state."""
    if not req.request.strip():
        raise HTTPException(status_code=400, detail="request is empty")

    run_id = uuid.uuid4().hex[:12]
    state = ContinuumState(request=req.request, run_id=run_id, started_at=time.time())
    _RUNS[run_id] = state

    # Run synchronously for M0 so the response carries the final state — small
    # requests finish in well under a second on the offline path. M1 will
    # background this via asyncio.create_task + GET /run/{id}/poll.
    try:
        await _execute_pipeline(state)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Pipeline failed for run %s", run_id)
        state.error_message = str(exc)

    return {
        "run_id": run_id,
        "status": _classify(state),
        "state": _state_to_dict(state),
    }


@app.get("/run/{run_id}")
async def get_run_status(run_id: str) -> Dict[str, Any]:
    """Return the recorded state for a previous run."""
    state = _RUNS.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found")
    return {
        "run_id": run_id,
        "status": _classify(state),
        "state": _state_to_dict(state),
    }


def _classify(state: ContinuumState) -> str:
    if state.human_approval_pending:
        return "escalated"
    if state.error_message:
        return "failed"
    if state.completed_at:
        return "complete"
    return "running"


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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
