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
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from orchestrator.agent_runner import AgentContext, run_agent
from orchestrator.events import event_bus
from orchestrator.gates import gate_scope_conformance, update_gate_status
from orchestrator.pdlc import emit_pdlc_artifacts
from orchestrator.state import AgentRole, ContinuumState
from evals.evidence_stack import build_evidence_stack
from api.compliance import build_compliance_report, render_compliance_html
from orchestrator.state_machine import derive_lifecycle_state

logger = logging.getLogger(__name__)

app = FastAPI(title="Continuum", version="0.2.0")


@app.on_event("startup")
async def validate_env() -> None:
    """
    Warn about missing optional env vars at startup.

    The server ALWAYS starts — even without any credentials — because the
    offline/stub path handles all cases.  This validator only logs warnings so
    operators know which integrations are in stub mode.
    """
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
    # M7: optional business mappings supplied at intent time (e.g. [{code:"BR", label:"Branch"}]).
    # Stored on the run state so gate_scope_conformance can enforce them post-implementation.
    business_mappings: List[Dict[str, str]] = []


class RejectRequest(BaseModel):
    """Body for POST /run/{run_id}/reject — return a story/design gate to the author."""
    gate: str           # "story_review" | "design_review"
    reason: str = ""


# M6: canonical run-lifecycle vocabulary surfaced to the Work Queue UI.
#   running | waiting_gate | blocked | returned | done | failed
# These are authoritative when set on the state; "running" falls back to the
# legacy derivation in _classify().
_EXPLICIT_STATES = {"waiting_gate", "blocked", "returned", "done", "failed"}

# Canonical stage ordering for the Work Queue progress indicator. The live
# pipeline runs `developer` as a single stage; it is mapped onto the backend
# slot so the index lands in the implementation band of the 8-stage spine.
_STAGE_INDEX: Dict[str, int] = {
    "bsa": 0,
    "architect": 1,
    "planner": 2,
    "database": 3,
    "backend": 4,
    "developer": 4,
    "frontend": 5,
    "security": 6,
    "memory": 7,
}
_STAGE_COUNT = 8


def _lifecycle_state(state: ContinuumState) -> str:
    """M13: best-effort lifecycle state for the run (read-only, never raises)."""
    try:
        return derive_lifecycle_state(state)
    except Exception:  # noqa: BLE001
        return "new"


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
        "run_status": _classify(state),
        "cost_usd": round(getattr(state, "cost_usd", 0.0), 4),
        "duration_s": _duration_s(state),
        "stage_idx": _stage_idx(state),
        "stage_count": _STAGE_COUNT,
        "reject_reason": state.reject_reason,
        "business_mappings": getattr(state, "business_mappings", []) or [],
        "mapping_fidelity": getattr(state, "mapping_fidelity", None),
        "pdlc_path": getattr(state, "pdlc_path", None),
        "component": getattr(state, "component", None),
        "spec_superseded": getattr(state, "spec_superseded", None),
        "lifecycle_state": _lifecycle_state(state),
        "started_at": state.started_at,
        "completed_at": state.completed_at,
    }


def _gate(state: ContinuumState, name: str) -> Optional[Any]:
    return next((g for g in state.gates if g.name == name), None)


def _classify(state: ContinuumState) -> str:
    """
    Map a run onto the canonical M6 vocabulary.

    Prefers state.run_status when it carries an explicit (non-"running") value;
    otherwise derives from the legacy flags so older code paths still classify.
    Note: the M6 vocabulary renames the old `awaiting_approval` → `waiting_gate`
    and `complete` → `done`.
    """
    rs = getattr(state, "run_status", "") or ""
    if rs in _EXPLICIT_STATES:
        return rs
    if state.human_approval_pending:
        return "waiting_gate"
    if state.error_message:
        return "failed"
    if state.completed_at:
        return "done"
    return "running"


def _duration_s(state: ContinuumState) -> Optional[float]:
    """Lead time in seconds: completed_at - started_at, or now - started_at if live."""
    if not state.started_at:
        return None
    end = state.completed_at or time.time()
    return round(end - state.started_at, 2)


def _stage_idx(state: ContinuumState) -> int:
    """Index of the current agent within the 8-stage spine (-1 if none/unknown)."""
    if state.current_agent is None:
        return -1
    return _STAGE_INDEX.get(state.current_agent.value, -1)


def _run_summary(state: ContinuumState) -> Dict[str, Any]:
    """Build a Work Queue row for a run (used by GET /runs)."""
    return {
        "run_id": state.run_id,
        "request": (state.request or "")[:80],
        "status": _classify(state),
        "run_status": _classify(state),
        "cost_usd": round(getattr(state, "cost_usd", 0.0), 4),
        "duration_s": _duration_s(state),
        "stage_idx": _stage_idx(state),
        "stage_count": _STAGE_COUNT,
        "started_at": state.started_at,
        "completed_at": state.completed_at,
        "current_agent": state.current_agent.value if state.current_agent else None,
        "reject_reason": getattr(state, "reject_reason", None),
    }


async def _mark_blocked(
    state: ContinuumState, gate_name: str, error_message: str, run_id: str = ""
) -> None:
    """
    Transition a run into the `blocked` state (M6): a gate stayed red past the
    retry budget. Keeps human_approval_pending set so the existing resume path
    and the new escalate-resolve endpoint can act on it.
    """
    state.run_status = "blocked"
    state.human_approval_pending = True
    state.approval_gate_name = gate_name
    if run_id:
        await event_bus.emit(run_id, {
            "event_type": "run_blocked",
            "agent": "developer",
            "run_id": run_id,
            "data": {"gate_name": gate_name, "error_message": (error_message or "")[:500]},
        })


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
            # M6: surface this as a first-class `blocked` run (distinct from the
            # story/design/merge waiting_gate). _mark_blocked keeps the approval
            # flag set so escalate-resolve / resume can re-drive the chain.
            await _mark_blocked(
                state, "local_verify", gate.error_message or "", run_id
            )
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

    # 2.5: M7 Scope-Guard gate — runs when business_mappings were supplied OR a
    # prior Registry spec exists for this component (M11 conformance), and the
    # developer chain succeeded (local_verify green or absent).
    bm = getattr(state, "business_mappings", []) or []
    has_prior = getattr(state, "registry_current_before", None) is not None
    if bm or has_prior:
        lv_check = _gate(state, "local_verify")
        if lv_check is None or lv_check.status == "green":
            sc_passed, sc_output = await gate_scope_conformance(state)
            update_gate_status(state, "scope_conformance", sc_passed, sc_output)
            if not sc_passed:
                # Scope mismatch → blocked (no auto-retry, per M7 spec).
                await _mark_blocked(state, "scope_conformance", sc_output, run_id)
                if run_id:
                    await event_bus.emit(run_id, {
                        "event_type": "human_gate_pending",
                        "agent": "developer",
                        "run_id": run_id,
                        "data": {
                            "gate_name": "scope_conformance",
                            "reason": "mapping_mismatch",
                        },
                    })
                logger.error("scope_conformance gate red — blocking run %s", run_id)
                return

    # 3. Security (only when local_verify is green)
    lv = _gate(state, "local_verify")
    if lv is None or lv.status == "green":
        await _run("security")

    # 4. M3: Memory agent — write episode regardless of gate outcomes so future
    #    runs can learn from both successes and failures.
    try:
        await _run("memory")
    except Exception as mem_exc:  # noqa: BLE001
        logger.warning("Memory agent failed (non-fatal): %s", mem_exc)

    state.completed_at = time.time()
    # M6: only mark `done` if we didn't get parked in blocked/returned/failed.
    if state.run_status not in _EXPLICIT_STATES:
        state.run_status = "done"

    # M8: emit .pdlc/ artifacts to the target repo (Layer 2) if configured.
    target_repo = os.getenv("CONTINUUM_TARGET_REPO", "").strip()
    if target_repo:
        try:
            pdlc_result = await emit_pdlc_artifacts(state, target_repo)
            state.pdlc_path = pdlc_result.get("pdlc_path", "")
            if run_id:
                await event_bus.emit(run_id, {
                    "event_type": "pdlc_written",
                    "agent": "pipeline",
                    "run_id": run_id,
                    "data": {
                        "pdlc_path": state.pdlc_path,
                        "files_written": pdlc_result.get("files_written", 0),
                    },
                })
            logger.info(
                "M8 pdlc: %d files → %s", pdlc_result.get("files_written", 0), state.pdlc_path
            )
        except Exception as pdlc_exc:  # noqa: BLE001
            logger.warning("M8: emit_pdlc_artifacts failed (non-fatal): %s", pdlc_exc)

    if run_id:
        await event_bus.emit(run_id, {
            "event_type": "run_complete",
            "agent": "",
            "run_id": run_id,
            "data": {
                "status": _classify(state),
                "episodes_written": len(state.episodes_written or []),
            },
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
    state = ContinuumState(
        request=req.request,
        run_id=run_id,
        started_at=time.time(),
        business_mappings=req.business_mappings,  # M7: scope-guard input
    )
    _RUNS[run_id] = state

    # Background task — emits events as each agent completes.
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
    return [_run_summary(s) for s in runs]


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
        artifacts = {
            "story": state.story,
            "component": state.component,
            "registry_specs": state.registry_specs,
            "spec_superseded": state.spec_superseded,
        }
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
    elif role == AgentRole.MEMORY.value:
        artifacts = {
            "episodes_retrieved": state.episodes or [],
            "episodes_written": state.episodes_written or [],
        }
    else:
        raise HTTPException(status_code=404, detail=f"unknown agent '{agent}'")

    return {"run_id": run_id, "agent": agent, "artifacts": artifacts}


@app.get("/specs")
async def list_specs() -> Dict[str, Any]:
    """M11: list components in the Spec Registry with their current version."""
    from graph_db import spec_registry
    return {"components": spec_registry.list_components()}


@app.get("/specs/{component}")
async def get_spec_chain(component: str) -> Dict[str, Any]:
    """M11: return the version chain (history + current) for a component."""
    from graph_db import spec_registry
    history = await spec_registry.get_spec_history(component)
    current = await spec_registry.get_current_spec(component)
    if not history and current is None:
        raise HTTPException(status_code=404, detail=f"no specs for component '{component}'")
    return {
        "component": component,
        "current": current,
        "history": history,
        "versions": len(history),
    }


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


@app.post("/run/{run_id}/reject")
async def reject_run(run_id: str, body: RejectRequest) -> Dict[str, Any]:
    """
    Return a run to its author at a story/design review gate (M6).

    Records run_status="returned" + the reason, clears the relevant approval
    flag, and emits `run_returned`. The run rests in `returned` for the operator
    to inspect (the Returned panel) and resubmit — it is not auto-restarted, so
    the status stays stable for the Work Queue.
    """
    state = _RUNS.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found")

    gate = body.gate or state.approval_gate_name or ""
    if gate == "story_review":
        state.story_approved = False
    elif gate == "design_review":
        state.design_approved = False

    state.run_status = "returned"
    state.reject_reason = body.reason or None
    state.human_approval_pending = False
    state.approval_gate_name = None

    await event_bus.emit(run_id, {
        "event_type": "run_returned",
        "agent": "",
        "run_id": run_id,
        "data": {"gate": gate, "reason": body.reason},
    })

    return {"run_id": run_id, "status": _classify(state), "gate": gate, "reason": body.reason}


@app.post("/run/{run_id}/escalate-resolve")
async def escalate_resolve(run_id: str) -> Dict[str, Any]:
    """
    Send a `blocked` run back to implementation (M6).

    Resets the blocking gate's retry budget, clears run_status back to
    `running`, and re-drives the developer chain (developer → security → memory)
    in the background — mirroring resume_run()'s local_verify branch.
    """
    state = _RUNS.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found")

    gate_name = state.approval_gate_name or "local_verify"
    gate = _gate(state, gate_name)
    if gate:
        gate.retry_count = 0
        gate.status = "pending"

    state.run_status = "running"
    state.human_approval_pending = False
    state.approval_gate_name = None
    state.completed_at = None

    await event_bus.emit(run_id, {
        "event_type": "human_gate_resolved",
        "agent": "developer",
        "run_id": run_id,
        "data": {"gate_name": gate_name, "approved": True, "action": "escalate_resolve"},
    })

    async def _redrive() -> None:
        try:
            ctx = AgentContext.from_env()
            ctx.run_id = run_id
            for _ in range(_MAX_RETRIES + 1):
                await run_agent(state, "developer", ctx)
                gv = _gate(state, "local_verify")
                if gv is None or gv.status == "green":
                    break
                if gv.retry_count >= _MAX_RETRIES:
                    await _mark_blocked(state, "local_verify", gv.error_message or "", run_id)
                    return
                gv.retry_count += 1
            lv = _gate(state, "local_verify")
            if lv is None or lv.status == "green":
                await run_agent(state, "security", ctx)
                try:
                    await run_agent(state, "memory", ctx)
                except Exception as mem_exc:  # noqa: BLE001
                    logger.warning("Memory agent failed (non-fatal): %s", mem_exc)
            state.completed_at = time.time()
            if state.run_status not in _EXPLICIT_STATES:
                state.run_status = "done"
            await event_bus.emit(run_id, {
                "event_type": "run_complete",
                "agent": "",
                "run_id": run_id,
                "data": {"status": _classify(state)},
            })
        except Exception as exc:  # noqa: BLE001
            logger.exception("escalate-resolve re-drive failed for run %s", run_id)
            state.error_message = str(exc)
            state.run_status = "failed"
            state.completed_at = time.time()

    asyncio.create_task(_redrive())
    return {"run_id": run_id, "status": _classify(state)}


@app.get("/runs/{run_id}/evidence")
async def get_evidence(run_id: str) -> Dict[str, Any]:
    """Return the 6-layer Evidence Stack for a run (M6)."""
    state = _RUNS.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found")
    return {"run_id": run_id, "evidence": build_evidence_stack(state)}


@app.get("/runs/{run_id}/compliance-report")
async def get_compliance_report(run_id: str) -> Dict[str, Any]:
    """M12: structured, auditor-readable compliance artifact for a run (JSON)."""
    state = _RUNS.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found")
    return build_compliance_report(run_id, state)


@app.get("/runs/{run_id}/compliance-report.html", response_class=HTMLResponse)
async def get_compliance_report_html(run_id: str) -> HTMLResponse:
    """M12: the same compliance report rendered as a standalone HTML page."""
    state = _RUNS.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found")
    report = build_compliance_report(run_id, state)
    return HTMLResponse(content=render_compliance_html(report))


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
