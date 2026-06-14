#!/usr/bin/env python
"""
verify_p1_gate_independence.py — P1.1 Gate Independence.

Splits the combined `local_verify` gate into three INDEPENDENT gates — lint,
typecheck, test — each with its own pass/fail and its own evidence record. This
makes the Evidence Stack's "6 independent signals" literally true (resolves OQ-3)
and keeps the offline path intact (each gate degrades to `py_compile`).

  1. The three gates are callable and green on clean code.
  2. Each gate independently reds a broken file (offline py_compile fallback).
  3. gate_local_verify_split returns the three results; gate_local_verify
     aggregates them (green iff all pass) — the backward-compatible composite.
  4. Evidence Stack OQ-3: with lint+typecheck green but test RED, layer 1 (build)
     is pass while layer 2 (regression) is fail — two genuinely independent signals.
  5. …and the inverse (lint RED, test green) flips exactly layer 1.
  6. Backward compatible: a state with only the composite `local_verify` reads it
     for both layers; and a live developer run records lint/typecheck/test
     independently on the state.

Usage:
  python scripts/verify_p1_gate_independence.py
Expected output:
  6/6 checks passed
"""
from __future__ import annotations

import asyncio
import io
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.evidence_stack import build_evidence_stack  # noqa: E402
from orchestrator import gates  # noqa: E402
from orchestrator.agent_runner import AgentContext, run_agent  # noqa: E402
from orchestrator.state import ContinuumState, GateStatus  # noqa: E402

CHECKS: list = []


def _check(name: str, passed: bool, detail: str = "") -> None:
    icon = "PASS" if passed else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"  [{icon}] {name}{suffix}")
    CHECKS.append((name, passed, detail))


def _clean_dir() -> str:
    d = tempfile.mkdtemp(prefix="p1_clean_")
    (Path(d) / "mod.py").write_text("x = 1\n\n\ndef f():\n    return x\n", encoding="utf-8")
    return d


def _broken_dir() -> str:
    d = tempfile.mkdtemp(prefix="p1_broken_")
    (Path(d) / "bad.py").write_text("def oops(:\n    return\n", encoding="utf-8")  # syntax error
    return d


async def main() -> int:
    print("P1.1 Gate Independence verification")
    print("=" * 50)

    clean, broken = _clean_dir(), _broken_dir()

    # ── Check 1: three gates callable + green on clean code ──────────────────
    l_ok, _ = await gates.gate_lint(clean)
    t_ok, _ = await gates.gate_typecheck(clean)
    x_ok, _ = await gates.gate_test(clean)
    _check(
        "gate_lint / gate_typecheck / gate_test exist and pass on clean code",
        l_ok and t_ok and x_ok,
        f"lint={l_ok}, typecheck={t_ok}, test={x_ok}",
    )

    # ── Check 2: each gate reds a broken file (py_compile fallback) ───────────
    bl_ok, _ = await gates.gate_lint(broken)
    bt_ok, _ = await gates.gate_typecheck(broken)
    # name the broken file like a test so the test gate's fallback picks it up
    test_broken = tempfile.mkdtemp(prefix="p1_testbad_")
    (Path(test_broken) / "test_bad.py").write_text("def x(:\n", encoding="utf-8")
    bx_ok, _ = await gates.gate_test(test_broken)
    _check(
        "each gate independently reds broken code",
        (not bl_ok) and (not bt_ok) and (not bx_ok),
        f"lint={bl_ok}, typecheck={bt_ok}, test={bx_ok}",
    )

    # ── Check 3: split returns three results; composite aggregates ───────────
    split = await gates.gate_local_verify_split(clean)
    comp_ok, _ = await gates.gate_local_verify(clean)
    comp_bad, _ = await gates.gate_local_verify(broken)
    _check(
        "split returns lint/typecheck/test; composite = AND of them",
        set(split.keys()) == {"lint", "typecheck", "test"} and comp_ok and (not comp_bad),
        f"keys={sorted(split.keys())}, composite_clean={comp_ok}, composite_broken={comp_bad}",
    )

    # ── Check 4: Evidence Stack OQ-3 — build pass while regression fails ──────
    st = ContinuumState(request="x")
    st.gates = [
        GateStatus(name="lint", status="green"),
        GateStatus(name="typecheck", status="green"),
        GateStatus(name="test", status="red", error_message="2 failed"),
    ]
    stack = build_evidence_stack(st)
    layer1, layer2 = stack[0], stack[1]
    _check(
        "Evidence Stack: layer 1 (build) pass, layer 2 (regression) fail — independent",
        len(stack) == 6 and layer1["status"] == "pass" and layer2["status"] == "fail",
        f"build={layer1['status']}, regression={layer2['status']}",
    )

    # ── Check 5: inverse — lint red flips only layer 1 ───────────────────────
    st2 = ContinuumState(request="x")
    st2.gates = [
        GateStatus(name="lint", status="red", error_message="E501"),
        GateStatus(name="typecheck", status="green"),
        GateStatus(name="test", status="green"),
    ]
    stack2 = build_evidence_stack(st2)
    _check(
        "Evidence Stack: lint red → layer 1 fail, layer 2 (test) still pass",
        stack2[0]["status"] == "fail" and stack2[1]["status"] == "pass",
        f"build={stack2[0]['status']}, regression={stack2[1]['status']}",
    )

    # ── Check 6: backward compat + live run records independent gates ─────────
    legacy = ContinuumState(request="x")
    legacy.gates = [GateStatus(name="local_verify", status="green")]
    legacy_stack = build_evidence_stack(legacy)
    legacy_ok = legacy_stack[0]["status"] == "pass" and legacy_stack[1]["status"] == "pass"

    run_state = ContinuumState(request="Build a tasks CRUD endpoint")
    ctx = AgentContext(repo_path=".")
    for role in ("bsa", "architect", "planner", "developer"):
        await run_agent(run_state, role, ctx)
    names = {g.name for g in run_state.gates}
    records_ok = {"lint", "typecheck", "test", "local_verify"}.issubset(names)

    _check(
        "legacy local_verify-only state still reads both layers; live run records 3 gates",
        legacy_ok and records_ok,
        f"legacy_ok={legacy_ok}, recorded={sorted(names & {'lint','typecheck','test','local_verify'})}",
    )

    # ── Summary ──────────────────────────────────────────────────────────────
    print()
    passed_n = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    print(f"{passed_n}/{total} checks passed")
    return 0 if passed_n == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
