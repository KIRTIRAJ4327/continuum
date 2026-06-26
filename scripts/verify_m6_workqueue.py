"""
verify_m6_workqueue.py -- prove the M6 Work Queue + Evidence Stack + run metrics
work end-to-end on the offline path (no Azure / Neo4j / Postgres).

Runs 6 checks:
  1. Offline pipeline run -> state.cost_usd accrues above 0.
  2. A clean run transitions run_status to "done".
  3. reject_run() returns a story/design gate -> run_status="returned" + reason.
  4. local_verify retry exhaustion -> run_status="blocked".
  5. build_evidence_stack(state) returns exactly 6 layers.
  6. _run_summary(state) carries run_status / cost_usd / duration_s / stage_idx.

Usage:
    python scripts/verify_m6_workqueue.py
"""
from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

# Force UTF-8 output on Windows.
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import main as api_main                                  # noqa: E402
from api.main import (                                            # noqa: E402
    RejectRequest,
    _execute_pipeline,
    _mark_blocked,
    _run_summary,
    reject_run,
)
from evals.evidence_stack import EVIDENCE_LAYER_COUNT, build_evidence_stack  # noqa: E402
from orchestrator.state import ContinuumState, GateStatus        # noqa: E402

_REQUEST = "Build a customer feedback portal with ratings and comments"

_CHECKS: list[tuple[str, bool]] = []


def _check(name: str, passed: bool, detail: str = "") -> None:
    status = "PASS" if passed else "FAIL"
    suffix = f"  [{detail}]" if detail else ""
    print(f"  [{status}] {name}{suffix}")
    _CHECKS.append((name, passed))


async def main() -> int:
    print()
    print("M6 Work Queue + Evidence Stack verification (6/6)")
    print("=" * 60)

    # ------------------------------------------------------------------
    # Checks 1 + 2: offline pipeline run -> cost accrues, run_status="done"
    # ------------------------------------------------------------------
    print("\nCheck 1/2: Offline pipeline run accrues cost and completes...")
    state = ContinuumState(request=_REQUEST, run_id="verify-m6-01")
    state.started_at = __import__("time").time()
    try:
        await _execute_pipeline(state)  # run_id="" -> silent, no events
        _check(
            "cost_usd accrues above 0 after a run",
            state.cost_usd > 0,
            f"cost_usd=${state.cost_usd:.4f}",
        )
        _check(
            "clean run transitions run_status to 'done'",
            state.run_status == "done",
            f"run_status={state.run_status}",
        )
    except Exception as exc:  # noqa: BLE001
        _check("cost_usd accrues above 0 after a run", False, str(exc))
        _check("clean run transitions run_status to 'done'", False, str(exc))

    # ------------------------------------------------------------------
    # Check 3: reject -> run_status="returned" + reject_reason
    # ------------------------------------------------------------------
    print("\nCheck 3: reject_run() returns a gate and records the reason...")
    try:
        rejected = ContinuumState(request=_REQUEST, run_id="verify-m6-reject")
        rejected.story_approved = True
        api_main._RUNS["verify-m6-reject"] = rejected
        # P0.3: reject_run now requires an authenticated principal with
        # REJECT_GATE; pass the ADMIN DEV_PRINCIPAL (the offline identity).
        from auth import DEV_PRINCIPAL
        await reject_run(
            "verify-m6-reject",
            RejectRequest(gate="story_review", reason="Acceptance criteria too vague"),
            principal=DEV_PRINCIPAL,
        )
        _check(
            "reject sets run_status='returned' + reason + clears approval",
            rejected.run_status == "returned"
            and rejected.reject_reason == "Acceptance criteria too vague"
            and rejected.story_approved is False,
            f"run_status={rejected.run_status}, reason={rejected.reject_reason!r}",
        )
    except Exception as exc:  # noqa: BLE001
        _check("reject sets run_status='returned' + reason + clears approval", False, str(exc))

    # ------------------------------------------------------------------
    # Check 4: retry exhaustion -> run_status="blocked"
    # ------------------------------------------------------------------
    print("\nCheck 4: local_verify retry exhaustion blocks the run...")
    try:
        blocked = ContinuumState(request=_REQUEST, run_id="verify-m6-blocked")
        blocked.gates.append(
            GateStatus(name="local_verify", status="red", retry_count=3, error_message="boom")
        )
        await _mark_blocked(blocked, "local_verify", "boom")
        _check(
            "retry exhaustion sets run_status='blocked'",
            blocked.run_status == "blocked" and blocked.approval_gate_name == "local_verify",
            f"run_status={blocked.run_status}",
        )
    except Exception as exc:  # noqa: BLE001
        _check("retry exhaustion sets run_status='blocked'", False, str(exc))

    # ------------------------------------------------------------------
    # Check 5: build_evidence_stack returns 6 layers
    # ------------------------------------------------------------------
    print("\nCheck 5: Evidence Stack returns 6 layers...")
    try:
        stack = build_evidence_stack(state)
        shapes_ok = all({"layer", "status", "detail"} <= set(layer) for layer in stack)
        _check(
            "build_evidence_stack() returns exactly 6 well-formed layers",
            len(stack) == EVIDENCE_LAYER_COUNT and shapes_ok,
            f"{len(stack)} layers: {[s['layer'] for s in stack]}",
        )
    except Exception as exc:  # noqa: BLE001
        _check("build_evidence_stack() returns exactly 6 well-formed layers", False, str(exc))

    # ------------------------------------------------------------------
    # Check 6: _run_summary carries the new Work Queue fields
    # ------------------------------------------------------------------
    print("\nCheck 6: Work Queue row carries run_status / cost / duration / stage...")
    try:
        row = _run_summary(state)
        required = {"run_status", "cost_usd", "duration_s", "stage_idx", "stage_count"}
        _check(
            "_run_summary() exposes run_status, cost_usd, duration_s, stage_idx",
            required <= set(row) and row["cost_usd"] > 0,
            f"run_status={row['run_status']}, cost=${row['cost_usd']}, "
            f"stage_idx={row['stage_idx']}/{row['stage_count']}",
        )
    except Exception as exc:  # noqa: BLE001
        _check("_run_summary() exposes run_status, cost_usd, duration_s, stage_idx", False, str(exc))

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print()
    print("=" * 60)
    passed = sum(1 for _, ok in _CHECKS if ok)
    total = len(_CHECKS)
    print(f"Result: {passed}/{total} PASS")
    print()

    if passed == total:
        print("M6 Work Queue + Evidence Stack: OK")
    else:
        print("FAIL - fix the checks above")
        for name, ok in _CHECKS:
            if not ok:
                print(f"  FAILED: {name}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
