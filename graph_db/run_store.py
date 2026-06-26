"""
run_store.py — P0.1 Durable Execution.

Two-tier run persistence for ContinuumState.

Tier 1 — Postgres (live): upserts the run JSON after each pipeline milestone
so runs survive a process restart.  Activated when a DSN is supplied and
asyncpg is importable.  Any connection or query failure degrades silently to
Tier 2.

Tier 2 — In-memory (offline default): the module-level ``_MEMORY`` dict.
``api/main.py`` imports this as ``_RUNS`` so they share the same object —
no singleton plumbing, no behaviour change for the verify suite.

State serialisation: key artifact fields only.  The LangGraph ``messages``
list is ephemeral control-flow and is NOT preserved across restarts.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Process-level store — api/main.py imports this object as _RUNS.
_MEMORY: Dict[str, Any] = {}

_DDL = """
CREATE TABLE IF NOT EXISTS continuum_runs (
    run_id      TEXT             PRIMARY KEY,
    state_json  JSONB            NOT NULL,
    run_status  TEXT             NOT NULL DEFAULT 'running',
    started_at  DOUBLE PRECISION,
    updated_at  DOUBLE PRECISION
);
"""


def _is_live(pool: Any) -> bool:
    return pool is not None


# --------------------------------------------------------------------------- #
# Serialisation helpers
# --------------------------------------------------------------------------- #

def _ev(val: Any) -> Any:
    """Extract .value from an enum (or return val if not an enum)."""
    return val.value if val is not None and hasattr(val, "value") else val


def serialize_state(state: Any) -> Dict[str, Any]:
    """Convert ContinuumState → JSON-safe dict (key artifact fields only)."""
    return {
        "run_id": state.run_id,
        "request": state.request,
        "current_agent": _ev(state.current_agent),
        "next_agent": _ev(getattr(state, "next_agent", None)),
        "story": state.story,
        "contract": state.contract,
        "schema": state.schema,
        "dag": state.dag,
        "code": state.code,
        "gates": [
            {
                "name": g.name,
                "status": g.status,
                "retry_count": g.retry_count,
                "error_message": g.error_message,
            }
            for g in (state.gates or [])
        ],
        "human_approval_pending": state.human_approval_pending,
        "approval_gate_name": state.approval_gate_name,
        "story_approved": state.story_approved,
        "design_approved": state.design_approved,
        "merge_approved": state.merge_approved,
        "error_message": state.error_message,
        "episodes": state.episodes or [],
        "episodes_written": state.episodes_written or [],
        "run_status": getattr(state, "run_status", "running"),
        "cost_usd": getattr(state, "cost_usd", 0.0),
        "reject_reason": getattr(state, "reject_reason", None),
        "business_mappings": getattr(state, "business_mappings", []) or [],
        "mapping_fidelity": getattr(state, "mapping_fidelity", None),
        "pdlc_path": getattr(state, "pdlc_path", None),
        "component": getattr(state, "component", None),
        "registry_specs": getattr(state, "registry_specs", []) or [],
        "registry_current_before": getattr(state, "registry_current_before", None),
        "spec_superseded": getattr(state, "spec_superseded", None),
        "lifecycle_state": _ev(getattr(state, "lifecycle_state", "new")),
        "incident_approved": getattr(state, "incident_approved", False),
        "started_at": state.started_at,
        "completed_at": state.completed_at,
    }


def deserialize_state(data: Dict[str, Any]) -> Any:
    """Reconstruct a ContinuumState from a serialised dict."""
    from orchestrator.state import AgentRole, ContinuumState, GateStatus
    from orchestrator.state_machine import SDLCState

    def _role(v: Any) -> Any:
        if v is None:
            return None
        try:
            return AgentRole(v)
        except (ValueError, KeyError):
            return None

    def _sdlc(v: Any) -> Any:
        try:
            return SDLCState(v) if v else SDLCState.NEW
        except (ValueError, KeyError):
            return SDLCState.NEW

    gates = [
        GateStatus(
            name=g["name"],
            status=g["status"],
            retry_count=g.get("retry_count", 0),
            error_message=g.get("error_message"),
        )
        for g in (data.get("gates") or [])
    ]

    state = ContinuumState(
        run_id=data.get("run_id", ""),
        request=data.get("request", ""),
        current_agent=_role(data.get("current_agent")),
        story=data.get("story"),
        contract=data.get("contract"),
        schema=data.get("schema"),
        dag=data.get("dag"),
        code=data.get("code"),
        gates=gates,
        human_approval_pending=data.get("human_approval_pending", False),
        approval_gate_name=data.get("approval_gate_name"),
        story_approved=data.get("story_approved", False),
        design_approved=data.get("design_approved", False),
        merge_approved=data.get("merge_approved", False),
        error_message=data.get("error_message"),
        episodes=data.get("episodes") or [],
        episodes_written=data.get("episodes_written") or [],
        run_status=data.get("run_status", "running"),
        cost_usd=data.get("cost_usd", 0.0),
        reject_reason=data.get("reject_reason"),
        business_mappings=data.get("business_mappings") or [],
        mapping_fidelity=data.get("mapping_fidelity"),
        pdlc_path=data.get("pdlc_path"),
        component=data.get("component"),
        registry_specs=data.get("registry_specs") or [],
        registry_current_before=data.get("registry_current_before"),
        spec_superseded=data.get("spec_superseded"),
        lifecycle_state=_sdlc(data.get("lifecycle_state")),
        incident_approved=data.get("incident_approved", False),
        started_at=data.get("started_at"),
        completed_at=data.get("completed_at"),
    )
    na = _role(data.get("next_agent"))
    if na is not None:
        state.next_agent = na
    return state


# --------------------------------------------------------------------------- #
# RunStore
# --------------------------------------------------------------------------- #

class RunStore:
    """
    Two-tier run store.

    Live: ``RunStore.create(dsn)`` — asyncpg pool to Postgres.  Persists after
    each milestone checkpoint so runs survive a process restart and can be
    resumed from their last known state on the next boot.

    Offline: ``RunStore.create()`` (no DSN) — wraps the module-level
    ``_MEMORY`` dict, which ``api/main._RUNS`` is an alias of.  Behaviour is
    identical to the pre-P0.1 in-memory dict.
    """

    def __init__(self, dsn: Optional[str] = None) -> None:
        self._dsn = dsn
        self._pool: Any = None

    @classmethod
    async def create(cls, dsn: Optional[str] = None) -> "RunStore":
        """Async factory: try Postgres, fall back to in-memory on any error."""
        store = cls(dsn=dsn)
        if dsn:
            try:
                import asyncpg  # type: ignore
                store._pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
                await store._setup()
                logger.info("[RunStore] Postgres pool ready — durable execution active")
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[RunStore] Postgres unavailable (%s); using in-memory store", exc
                )
                store._pool = None
        return store

    async def _setup(self) -> None:
        """Create the continuum_runs table if it doesn't exist (idempotent)."""
        if not _is_live(self._pool):
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(_DDL)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[RunStore] _setup() failed: %s", exc)

    @property
    def memory(self) -> Dict[str, Any]:
        """The module-level in-memory dict — same object as api/main._RUNS."""
        return _MEMORY

    async def save(self, run_id: str, state: Any) -> None:
        """
        Persist a checkpoint.

        Offline: no-op (state is already in _MEMORY via _RUNS).
        Live:    upsert to Postgres so the run survives a process restart.
        """
        if not _is_live(self._pool):
            return
        try:
            data = serialize_state(state)
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO continuum_runs
                        (run_id, state_json, run_status, started_at, updated_at)
                    VALUES ($1, $2::jsonb, $3, $4, $5)
                    ON CONFLICT (run_id) DO UPDATE
                        SET state_json = EXCLUDED.state_json,
                            run_status = EXCLUDED.run_status,
                            updated_at = EXCLUDED.updated_at
                    """,
                    run_id,
                    json.dumps(data),
                    data.get("run_status", "running"),
                    data.get("started_at"),
                    time.time(),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[RunStore] save(%s) failed: %s", run_id, exc)

    async def load(self, run_id: str) -> Optional[Any]:
        """
        Load a run by ID.

        Checks _MEMORY first (populated for the current process); falls back
        to Postgres (for runs started in a previous process).
        """
        if run_id in _MEMORY:
            return _MEMORY[run_id]
        if not _is_live(self._pool):
            return None
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT state_json FROM continuum_runs WHERE run_id = $1",
                    run_id,
                )
            if row:
                state = deserialize_state(json.loads(row["state_json"]))
                _MEMORY[run_id] = state
                return state
        except Exception as exc:  # noqa: BLE001
            logger.warning("[RunStore] load(%s) failed: %s", run_id, exc)
        return None

    async def list_recent(self, n: int = 50) -> List[str]:
        """Return up to n recent run_ids (newest first)."""
        if not _is_live(self._pool):
            return list(_MEMORY.keys())
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT run_id FROM continuum_runs ORDER BY started_at DESC LIMIT $1",
                    n,
                )
            return [r["run_id"] for r in rows]
        except Exception as exc:  # noqa: BLE001
            logger.warning("[RunStore] list_recent() failed: %s", exc)
            return list(_MEMORY.keys())

    async def restore_active(self) -> int:
        """
        On startup: load all non-terminal runs from Postgres into _MEMORY.

        Skips run_ids already present in _MEMORY (current-process runs).
        Returns the count of runs restored.
        """
        if not _is_live(self._pool):
            return 0
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT run_id, state_json
                    FROM   continuum_runs
                    WHERE  run_status NOT IN ('done','failed')
                    ORDER  BY started_at DESC
                    LIMIT  200
                    """,
                )
            restored = 0
            for row in rows:
                rid = row["run_id"]
                if rid not in _MEMORY:
                    _MEMORY[rid] = deserialize_state(json.loads(row["state_json"]))
                    restored += 1
            if restored:
                logger.info("[RunStore] restored %d active runs from Postgres", restored)
            return restored
        except Exception as exc:  # noqa: BLE001
            logger.warning("[RunStore] restore_active() failed: %s", exc)
            return 0
