"""
Verify the M0 dev→gate→retry loop terminates.

Covers three flows the orchestrator must handle:

  1. Green path — local_verify passes, security runs, no escalation.
  2. Red path with eventual green — gate flips green on retry < 3, no escalation.
  3. Persistent red — 3 retries, then human_approval_pending = True.

Uses the same pipeline driver as the FastAPI route so what we verify here is
what `make run` will execute. Runs offline (no langgraph, no live model).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Tuple
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.main import _execute_pipeline  # noqa: E402
from orchestrator.state import ContinuumState  # noqa: E402


def _gate(state: ContinuumState, name: str):
    return next((g for g in state.gates if g.name == name), None)


async def case_green() -> Tuple[str, bool, str]:
    state = ContinuumState(request="Build a product dashboard")
    await _execute_pipeline(state)
    lv = _gate(state, "local_verify")
    sec = _gate(state, "security_sast")
    ok = (
        lv is not None
        and lv.status == "green"
        and sec is not None
        and not state.human_approval_pending
        and state.completed_at is not None
        and bool(state.story) and bool(state.contract) and bool(state.schema)
    )
    msg = f"local_verify={lv and lv.status} sec={sec and sec.status} escalated={state.human_approval_pending}"
    return "green path completes", ok, msg


async def case_red_then_green() -> Tuple[str, bool, str]:
    """Patch the gate to fail twice, then succeed on attempt 3 (retry_count==2)."""
    state = ContinuumState(request="Build a flaky feature")
    calls = {"n": 0}

    async def fake_local_verify(_path: str):
        calls["n"] += 1
        if calls["n"] < 3:
            return False, f"simulated lint failure #{calls['n']}"
        return True, "ok"

    with patch("orchestrator.gates.gate_local_verify", new=fake_local_verify):
        await _execute_pipeline(state)

    lv = _gate(state, "local_verify")
    ok = (
        calls["n"] == 3
        and lv is not None
        and lv.status == "green"
        and not state.human_approval_pending
    )
    return "red-then-green within retry budget", ok, f"calls={calls['n']} status={lv and lv.status}"


async def case_persistent_red() -> Tuple[str, bool, str]:
    """Gate always fails — pipeline must escalate after 3 retries, not loop."""
    state = ContinuumState(request="Build a doomed feature")

    async def always_fail(_path: str):
        return False, "simulated permanent failure"

    with patch("orchestrator.gates.gate_local_verify", new=always_fail):
        await _execute_pipeline(state)

    lv = _gate(state, "local_verify")
    ok = (
        state.human_approval_pending is True
        and state.approval_gate_name == "local_verify"
        and lv is not None
        and lv.status == "red"
        and lv.retry_count == 3
    )
    return "persistent red escalates after 3 retries", ok, (
        f"escalated={state.human_approval_pending} retries={lv and lv.retry_count}"
    )


async def main() -> int:
    results = []
    for case in (case_green, case_red_then_green, case_persistent_red):
        label, ok, detail = await case()
        results.append((label, ok, detail))

    print("\nM0 loop verification")
    print("=" * 56)
    all_ok = True
    for label, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        print(f"         {detail}")
        all_ok = all_ok and ok
    print("=" * 56)
    print("RESULT:", "ALL PASS" if all_ok else "FAILURES")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
