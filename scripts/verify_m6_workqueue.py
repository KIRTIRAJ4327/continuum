"""
verify_m6_workqueue.py — M6 Work Queue + Evidence Stack offline verification.

Runs 6 checks:
  1. run_status defaults to "running" on new state
  2. cost_usd accumulates after agent runs (> 0)
  3. run_status → "done" after full pipeline
  4. run_status → "blocked" after local_verify retry exhaustion
  5. reject endpoint sets run_status → "returned" with reject_reason
  6. build_evidence_stack() returns 6 layers

Usage:
    python scripts/verify_m6_workqueue.py
"""
from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path
from unittest.mock import patch

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.agent_runner import AgentContext, run_agent  # noqa: E402
from orchestrator.state import ContinuumState, GateStatus     # noqa: E402

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

    # ── Check 1: run_status default ────────────────────────────────────────
    print("\nCheck 1: run_status defaults to 'running'")
    try:
        state = ContinuumState(request="test feature")
        _check("run_status default", state.run_status == "running", state.run_status)
    except Exception as exc:
        _check("run_status default", False, str(exc))

    # ── Check 2: cost_usd accumulates ─────────────────────────────────────
    print("\nCheck 2: cost_usd accumulates after offline agent runs")
    try:
        state = ContinuumState(request="add login page", run_id="verify-m6-cost")
        ctx = AgentContext(repo_path=".")
        await run_agent(state, "bsa", ctx)
        _check("cost_usd > 0 after bsa", state.cost_usd > 0, f"${state.cost_usd:.4f}")
    except Exception as exc:
        _check("cost_usd > 0 after bsa", False, str(exc))

    # ── Check 3: run_status → "done" after full pipeline ──────────────────
    print("\nCheck 3: run_status set to 'done' after successful pipeline")
    try:
        from api.main import _execute_pipeline
        state = ContinuumState(request="build dashboard", run_id="verify-m6-done")
        await _execute_pipeline(state)
        _check("run_status == 'done'", state.run_status == "done", state.run_status)
    except Exception as exc:
        _check("run_status == 'done'", False, str(exc))

    # ── Check 4: run_status → "blocked" on retry exhaustion ───────────────
    print("\nCheck 4: run_status set to 'blocked' when local_verify exhausts retries")
    try:
        from api.main import _execute_pipeline, _MAX_RETRIES
        state = ContinuumState(request="build auth service", run_id="verify-m6-blocked")

        # Inject a permanently-failing local_verify gate
        red_gate = GateStatus(name="local_verify", status="red", retry_count=_MAX_RETRIES)
        state.gates.append(red_gate)

        with patch("api.main.run_agent") as mock_run:
            async def fake_run(st, role, ctx):
                # Developer agent: keep gate red
                if role == "developer":
                    g = next((g for g in st.gates if g.name == "local_verify"), None)
                    if g:
                        g.status = "red"
                        g.retry_count = _MAX_RETRIES
                return st
            mock_run.side_effect = fake_run
            await _execute_pipeline(state)

        _check(
            "run_status == 'blocked'",
            state.run_status == "blocked",
            state.run_status,
        )
        _check(
            "human_approval_pending True",
            state.human_approval_pending,
            str(state.human_approval_pending),
        )
    except Exception as exc:
        _check("run_status == 'blocked'", False, str(exc))
        _check("human_approval_pending True", False, str(exc))

    # ── Check 5: reject sets run_status → "returned" ──────────────────────
    print("\nCheck 5: reject_run sets run_status='returned' with reject_reason")
    try:
        from api.main import _RUNS, reject_run, RejectRequest
        state = ContinuumState(
            request="update user profile",
            run_id="verify-m6-reject",
            run_status="waiting_gate",
        )
        _RUNS["verify-m6-reject"] = state
        try:
            await reject_run("verify-m6-reject", RejectRequest(gate="story_review", reason="Scope too broad"))
            _check("run_status == 'returned'", state.run_status == "returned", state.run_status)
            _check("reject_reason set", state.reject_reason == "Scope too broad", state.reject_reason or "")
        finally:
            _RUNS.pop("verify-m6-reject", None)
    except Exception as exc:
        _check("run_status == 'returned'", False, str(exc))
        _check("reject_reason set", False, str(exc))

    # ── Check 6: build_evidence_stack returns 6 layers ────────────────────
    print("\nCheck 6: build_evidence_stack() returns exactly 6 layers")
    try:
        from evals.evidence_stack import build_evidence_stack
        state = ContinuumState(request="build api endpoint")
        stack = build_evidence_stack(state)
        _check("6 layers returned", len(stack) == 6, f"got {len(stack)}")
        _check(
            "all layers have required keys",
            all("layer" in lay and "name" in lay and "status" in lay for lay in stack),
            str([lay.get("name") for lay in stack]),
        )
    except Exception as exc:
        _check("6 layers returned", False, str(exc))
        _check("all layers have required keys", False, str(exc))

    # ── Report ─────────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    passed = sum(1 for _, ok in _CHECKS if ok)
    total = len(_CHECKS)
    status = "PASS" if passed == total else "FAIL"
    print(f"Result: {passed}/{total} [{status}]")
    if passed < total:
        print("\nFailed checks:")
        for name, ok in _CHECKS:
            if not ok:
                print(f"  ✗ {name}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
