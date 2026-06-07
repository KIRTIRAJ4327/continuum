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


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
