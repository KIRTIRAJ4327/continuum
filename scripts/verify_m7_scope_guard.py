#!/usr/bin/env python
"""
verify_m7_scope_guard.py — M7 Mapping Fidelity (Scope-Guard) offline verification.

Two test scenarios:
  1. Exact match — all supplied codes found in generated code, none extra → gate green.
  2. Extra code  — generated code has a code that wasn't supplied → gate red → blocked.

Usage:
  python scripts/verify_m7_scope_guard.py
Expected output:
  2/2 checks passed
"""
from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.gates import gate_scope_conformance
from orchestrator.state import ContinuumState

CHECKS: list = []


def _check(name: str, passed: bool, detail: str = "") -> None:
    icon = "PASS" if passed else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"  [{icon}] {name}{suffix}")
    CHECKS.append((name, passed, detail))


async def main() -> int:
    print("M7 Scope-Guard verification")
    print("=" * 50)

    # ── Case A: exact match → gate green ─────────────────────────────────────
    state_a = ContinuumState(request="add branch management")
    state_a.business_mappings = [
        {"code": "BR", "label": "Branch"},
        {"code": "ACC", "label": "Account"},
    ]
    # Generated code uses exactly the supplied codes as dict keys — nothing extra.
    state_a.code = {
        "models.py": (
            'MAPPING = {\n'
            '    "BR": "Branch",\n'
            '    "ACC": "Account",\n'
            '}\n'
        )
    }

    ok_a, detail_a = await gate_scope_conformance(state_a)
    mf_a = state_a.mapping_fidelity or {}
    _check(
        "Case A: exact match → gate green",
        ok_a
        and mf_a.get("exact_match") is True
        and set(mf_a.get("found", [])) == {"BR", "ACC"},
        detail_a,
    )

    # ── Case B: extra code in generated output → gate red → run blocked ──────
    state_b = ContinuumState(request="add branch management")
    state_b.business_mappings = [{"code": "BR", "label": "Branch"}]
    # Generated code has BR (supplied) PLUS an uninvited EXTRA key.
    state_b.code = {
        "models.py": (
            'MAPPING = {\n'
            '    "BR": "Branch",\n'
            '    "EXTRA": "NotInSpec",\n'
            '}\n'
        )
    }

    ok_b, detail_b = await gate_scope_conformance(state_b)
    mf_b = state_b.mapping_fidelity or {}
    gate_red = not ok_b
    extra_flagged = "EXTRA" in mf_b.get("extra_in_code", [])
    _check(
        "Case B: extra mapping in code → gate red (blocked with specific reason)",
        gate_red and extra_flagged,
        detail_b,
    )

    # ── Summary ──────────────────────────────────────────────────────────────
    print()
    passed_n = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    print(f"{passed_n}/{total} checks passed")
    return 0 if passed_n == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
