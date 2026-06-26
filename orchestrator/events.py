"""
In-memory event bus for real-time agent progress.

Subscribers receive a Queue + the full historical log for that run, so an SSE
client that connects mid-run does not miss earlier events.

Usage (emitter side — orchestrator/agent_runner.py):
    from orchestrator.events import event_bus
    await event_bus.emit(run_id, {"event_type": "agent_start", ...})

Usage (subscriber side — api/main.py SSE endpoint):
    q, history = event_bus.subscribe(run_id)
    try:
        for ev in history:
            yield f"data: {json.dumps(ev)}\\n\\n"
        while True:
            ev = await asyncio.wait_for(q.get(), timeout=30)
            yield f"data: {json.dumps(ev)}\\n\\n"
    finally:
        event_bus.unsubscribe(run_id, q)
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Maximum events stored per run (prevents unbounded memory growth).
_MAX_LOG = 500


# --------------------------------------------------------------------------- #
# M10: optional OpenTelemetry export (offline-safe, opt-in, best-effort)
# --------------------------------------------------------------------------- #
# Every event the bus broadcasts is *also* mirrored onto an OTel span event when
# BOTH (a) CONTINUUM_OTEL is truthy AND (b) the `opentelemetry-api` package is
# importable. Absent either, `_otel_emit` is a no-op — the canonical offline
# path never imports OTel and never changes behaviour. Any OTel error is
# swallowed so telemetry can never break a run.
_OTEL_ENV_FLAG = "CONTINUUM_OTEL"
_otel_tracer: Any = None
_otel_checked = False


def otel_enabled() -> bool:
    """True iff OTel export is opted in via env AND the package is importable."""
    if not os.getenv(_OTEL_ENV_FLAG):
        return False
    return _get_tracer() is not None


def _get_tracer() -> Optional[Any]:
    """Lazily resolve an OTel tracer; cache the (possibly None) result."""
    global _otel_tracer, _otel_checked
    if _otel_checked:
        return _otel_tracer
    _otel_checked = True
    try:
        from opentelemetry import trace  # type: ignore[import]

        _otel_tracer = trace.get_tracer("continuum.events")
    except Exception:  # noqa: BLE001 — package absent or misconfigured → no-op
        _otel_tracer = None
    return _otel_tracer


def _otel_emit(run_id: str, event: Dict[str, Any]) -> None:
    """Mirror an event onto an OTel span event. No-op unless opted in + installed."""
    if not os.getenv(_OTEL_ENV_FLAG):
        return
    tracer = _get_tracer()
    if tracer is None:
        return
    try:
        et = str(event.get("event_type", "event"))
        with tracer.start_as_current_span(f"continuum.{et}") as span:
            span.set_attribute("continuum.run_id", run_id)
            span.set_attribute("continuum.event_type", et)
            if event.get("agent"):
                span.set_attribute("continuum.agent", str(event["agent"]))
            if event.get("gate"):
                span.set_attribute("continuum.gate", str(event["gate"]))
    except Exception:  # noqa: BLE001 — telemetry must never break a run
        pass


def _reset_otel_cache() -> None:
    """Test hook: clear the cached tracer so env changes take effect."""
    global _otel_tracer, _otel_checked
    _otel_tracer = None
    _otel_checked = False

# Event types are plain `event_type` strings on the emitted dict — there is no
# enum. The full vocabulary (keep the UI's EVENT_STYLES map in sync):
#   agent_start, agent_complete, gate_green, gate_red, gate_retry,
#   human_gate_pending, human_gate_resolved, run_complete
# M6 adds two run-lifecycle events:
#   run_blocked  — a gate stayed red past max retries; data: {gate_name, error_message}
#   run_returned — a human rejected a story/design gate; data: {gate, reason}
# C2 (observability cockpit) adds finer-grained events so the UI can show what
# the backend is actually doing, not just a spinner. All are offline-safe and
# carry the per-run `seq` (C1) like every other event:
#   tool_call       data: {tool_name, args_preview, result_preview, duration_s}
#   llm_token       data: {token, cumulative_tokens}    (opt-in CONTINUUM_STREAM_TOKENS)
#   agent_thinking  data: {thought}     — a reasoning step before an action
#   agent_milestone data: {message}     — a named checkpoint (e.g. "4 files planned")
#   artifact_ready  data: {artifact_type, preview}
#   sensor_result   data: {sensor, status, detail}   — one per gate sensor (ruff/mypy/pytest/bandit)
#   scope_checked   data: {exact_match, extra_in_code, missing_in_code}
#   evidence_built  data: {layers: [{layer, status, detail}, ...]}
#   controlled_hold data: {reason, gate, retry_count} — run parked (blocked)


class _EventBus:
    """Broadcast-style per-run event bus with full history replay."""

    def __init__(self) -> None:
        # run_id → list of subscriber queues
        self._queues: Dict[str, List[asyncio.Queue]] = {}
        # run_id → ordered event log (for late-joining subscribers)
        self._log: Dict[str, List[Dict[str, Any]]] = {}
        # C1: run_id → next monotonic seq. Set ONCE per event on emit; never
        # renumbered on replay. Enables SSE `id:` frames + Last-Event-ID resume.
        self._seq: Dict[str, int] = {}

    # ------------------------------------------------------------------ #
    # Emit
    # ------------------------------------------------------------------ #
    async def emit(self, run_id: str, event: Dict[str, Any]) -> None:
        """Broadcast an event to all current subscribers and append to log."""
        if not run_id:
            return
        event.setdefault("timestamp", time.time())
        # C1: stamp a per-run monotonic seq exactly once (idempotent if already set).
        if "seq" not in event:
            seq = self._seq.get(run_id, 0)
            event["seq"] = seq
            self._seq[run_id] = seq + 1
        log = self._log.setdefault(run_id, [])
        if len(log) < _MAX_LOG:
            log.append(event)
        # M10: best-effort OTel mirror (no-op unless opted in + package present).
        _otel_emit(run_id, event)
        for q in list(self._queues.get(run_id, [])):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("[events] Queue full for run %s — dropping event", run_id)

    def emit_nowait(self, run_id: str, event: Dict[str, Any]) -> None:
        """
        Fire-and-forget variant safe to call from synchronous code.
        Schedules the coroutine on the current running loop (best-effort).
        """
        if not run_id:
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.emit(run_id, event))
        except RuntimeError:
            pass  # No running loop (e.g. offline tests) — safe to ignore

    # ------------------------------------------------------------------ #
    # Subscribe / Unsubscribe
    # ------------------------------------------------------------------ #
    def subscribe(self, run_id: str) -> Tuple[asyncio.Queue, List[Dict[str, Any]]]:
        """
        Register a new subscriber for a run.

        Returns (queue, history) where history is a snapshot of all events
        emitted before this subscribe call, and queue will receive every event
        emitted afterwards.
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._queues.setdefault(run_id, []).append(q)
        history = list(self._log.get(run_id, []))
        return q, history

    def unsubscribe(self, run_id: str, q: asyncio.Queue) -> None:
        """Remove a subscriber queue (called in the SSE generator's finally block)."""
        subs = self._queues.get(run_id, [])
        if q in subs:
            subs.remove(q)

    # ------------------------------------------------------------------ #
    # Cleanup
    # ------------------------------------------------------------------ #
    def purge(self, run_id: str) -> None:
        """Drop all state for a completed run (call when run is finalized)."""
        self._queues.pop(run_id, None)
        self._log.pop(run_id, None)
        self._seq.pop(run_id, None)  # C1: reset the monotonic counter too

    def run_history(self, run_id: str) -> List[Dict[str, Any]]:
        """Return a copy of the event log for a run (used by the artifact endpoint)."""
        return list(self._log.get(run_id, []))


# Module-level singleton — import this everywhere.
event_bus = _EventBus()
