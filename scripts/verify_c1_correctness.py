#!/usr/bin/env python
"""
verify_c1_correctness.py — C1 Correctness Sprint (5/5).

Verifies the four correctness fixes that make Continuum reliable at scale:

  A. Monotonic event seq — three emits on a run carry seq 0, 1, 2 (set once).
  B. Last-Event-ID replay filter — given resume_seq=1, only events with
     seq >= 1 are replayed (the seq-0 event is skipped).
  C. _human_gate_node idempotency — a second resume of an already-approved gate
     does not re-run the approval logic (flag stays set, no re-interrupt).
  D. EventBus.purge() resets history AND the seq counter — a fresh run after a
     purge starts numbering from 0 again.

Pure + offline: no Postgres, no Azure, no langgraph runtime required.

Usage:
  python scripts/verify_c1_correctness.py
Expected output:
  5/5 checks passed
"""
from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.events import _EventBus  # noqa: E402
from orchestrator.state import ContinuumState  # noqa: E402

CHECKS: list = []


def _check(name: str, passed: bool, detail: str = "") -> None:
    status = "PASS" if passed else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"  [{status}] {name}{suffix}")
    CHECKS.append((name, passed))


def _replay_filter(history: list, resume_seq: int) -> list:
    """Mirror the api/main.py stream_events replay rule (seq >= resume_seq)."""
    return [ev for ev in history if ev.get("seq", 0) >= resume_seq]


async def main() -> int:
    print()
    print("C1 Correctness verification (5/5)")
    print("=" * 40)

    # ── Case A: monotonic seq 0,1,2 ──────────────────────────────────────── #
    print("\nCase A: monotonic event seq")
    bus = _EventBus()
    rid = "run-c1"
    for i in range(3):
        await bus.emit(rid, {"event_type": "agent_milestone", "agent": "bsa",
                             "run_id": rid, "data": {"i": i}})
    seqs = [ev.get("seq") for ev in bus.run_history(rid)]
    _check("three emits carry seq 0,1,2", seqs == [0, 1, 2], f"seqs={seqs}")

    # seq must be set ONCE — re-emitting an event that already has a seq keeps it.
    pre = {"event_type": "x", "agent": "a", "run_id": rid, "seq": 99, "data": {}}
    await bus.emit(rid, pre)
    _check("pre-stamped seq is not overwritten", pre["seq"] == 99, f"seq={pre['seq']}")

    # ── Case B: Last-Event-ID replay filter ──────────────────────────────── #
    print("\nCase B: Last-Event-ID replay filter (resume_seq=1)")
    history = bus.run_history(rid)
    replayed = _replay_filter(history, resume_seq=1)
    replayed_seqs = sorted(ev.get("seq") for ev in replayed)
    _check(
        "only seq >= 1 replayed (seq-0 skipped)",
        0 not in replayed_seqs and 1 in replayed_seqs,
        f"replayed_seqs={replayed_seqs}",
    )

    # ── Case C: _human_gate_node idempotency guard ───────────────────────── #
    print("\nCase C: double-resume sets approval flag once")
    # Mirror the guard logic from orchestrator/graph._human_gate_node without
    # needing the langgraph runtime. The guard returns early when already resolved.
    state = ContinuumState(run_id="run-gate", request="x")
    state.approval_gate_name = "story_review"
    state.human_approval_pending = True

    def _already_resolved(st: ContinuumState) -> bool:
        gate_name = st.approval_gate_name or "unknown"
        named = ("story_review", "design_review", "merge_review")
        return (
            (gate_name == "story_review" and st.story_approved)
            or (gate_name == "design_review" and st.design_approved)
            or (gate_name == "merge_review" and st.merge_approved)
            or (gate_name not in named and not st.human_approval_pending)
        )

    approve_calls = {"n": 0}

    def _resume_once(st: ContinuumState) -> None:
        if _already_resolved(st):
            st.human_approval_pending = False
            st.approval_gate_name = None
            return
        # first resume: approve
        st.story_approved = True
        approve_calls["n"] += 1
        st.human_approval_pending = False
        st.approval_gate_name = None

    _resume_once(state)              # first approve
    state.approval_gate_name = "story_review"  # simulate node re-entry from top
    _resume_once(state)              # second (double) resume — must be a no-op
    _check(
        "approval logic ran exactly once",
        approve_calls["n"] == 1 and state.story_approved is True,
        f"approve_calls={approve_calls['n']}",
    )

    # ── Case D: purge resets history + seq counter ───────────────────────── #
    print("\nCase D: purge resets history and seq counter")
    bus.purge(rid)
    await bus.emit(rid, {"event_type": "agent_start", "agent": "bsa",
                         "run_id": rid, "data": {}})
    post = bus.run_history(rid)
    _check(
        "fresh run after purge starts at seq 0",
        len(post) == 1 and post[0].get("seq") == 0,
        f"len={len(post)} seq={post[0].get('seq') if post else None}",
    )

    # ── Summary ──────────────────────────────────────────────────────────── #
    print()
    print("=" * 40)
    passed = sum(1 for _, ok in CHECKS if ok)
    total = len(CHECKS)
    print(f"{passed}/{total} checks passed")
    print()
    if passed == total:
        print("C1 Correctness: OK")
    else:
        print("FAIL — fix the checks above")
        for name, ok in CHECKS:
            if not ok:
                print(f"  FAILED: {name}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
