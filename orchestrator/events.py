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
import time
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

# Maximum events stored per run (prevents unbounded memory growth).
_MAX_LOG = 500


class _EventBus:
    """Broadcast-style per-run event bus with full history replay."""

    def __init__(self) -> None:
        # run_id → list of subscriber queues
        self._queues: Dict[str, List[asyncio.Queue]] = {}
        # run_id → ordered event log (for late-joining subscribers)
        self._log: Dict[str, List[Dict[str, Any]]] = {}

    # ------------------------------------------------------------------ #
    # Emit
    # ------------------------------------------------------------------ #
    async def emit(self, run_id: str, event: Dict[str, Any]) -> None:
        """Broadcast an event to all current subscribers and append to log."""
        if not run_id:
            return
        event.setdefault("timestamp", time.time())
        log = self._log.setdefault(run_id, [])
        if len(log) < _MAX_LOG:
            log.append(event)
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

    def run_history(self, run_id: str) -> List[Dict[str, Any]]:
        """Return a copy of the event log for a run (used by the artifact endpoint)."""
        return list(self._log.get(run_id, []))


# Module-level singleton — import this everywhere.
event_bus = _EventBus()
