#!/usr/bin/env python
"""
verify_m9_maf_pilot.py — M9 Microsoft Agent Framework (MAF) harness pilot.

Proves the pilot is correct AND dormant in the offline / thin environment (no
`agent_framework` package, no Azure credentials):

  1. maf_package_available() is False here (package absent) — capability gate works.
  2. should_use_maf("backend") is False by default (CONTINUUM_MAF_AGENTS unset) —
     the pilot is opt-in, off by default.
  3. should_use_maf("backend") stays False even with the flag set, because the
     package is absent — BOTH conditions (opt-in AND installed) are required.
  4. maf_enabled_roles() parses CONTINUUM_MAF_AGENTS correctly.
  5. run_maf_agent() raises MAFUnavailable when the package is absent — the
     fallback trigger fires deterministically.
  6. Fallback routing: with a model resolved + role opted in + package absent,
     run_agent() degrades to the LangChain loop (so a live run never breaks).
     Plus: the pure offline backend run still produces code (invariant intact).

Usage:
  python scripts/verify_m9_maf_pilot.py
Expected output:
  6/6 checks passed
"""
from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

import os

from orchestrator import agent_runner, maf_runner
from orchestrator.maf_runner import (
    MAFUnavailable,
    maf_enabled_roles,
    maf_package_available,
    run_maf_agent,
    should_use_maf,
)
from orchestrator.state import ContinuumState

CHECKS: list = []


def _check(name: str, passed: bool, detail: str = "") -> None:
    icon = "PASS" if passed else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"  [{icon}] {name}{suffix}")
    CHECKS.append((name, passed, detail))


async def main() -> int:
    print("M9 MAF Harness Pilot verification")
    print("=" * 50)

    # Ensure a clean env (the flag may leak from the parent shell).
    os.environ.pop(maf_runner.MAF_ENV_FLAG, None)

    # ── Check 1: package absent in the thin environment ──────────────────────
    pkg = maf_package_available()
    _check(
        "agent_framework package is absent (capability gate)",
        pkg is False,
        f"maf_package_available()={pkg}",
    )

    # ── Check 2: opt-in, off by default ──────────────────────────────────────
    default_off = should_use_maf("backend")
    _check(
        "should_use_maf('backend') is False by default (opt-in)",
        default_off is False,
        f"flag unset → {default_off}",
    )

    # ── Check 3: both conditions required (flag set, package still absent) ────
    os.environ[maf_runner.MAF_ENV_FLAG] = "backend"
    with_flag = should_use_maf("backend")
    _check(
        "should_use_maf('backend') stays False without the package",
        with_flag is False,
        f"flag set but package absent → {with_flag}",
    )

    # ── Check 4: env parsing ─────────────────────────────────────────────────
    os.environ[maf_runner.MAF_ENV_FLAG] = "backend, Frontend "
    roles = maf_enabled_roles()
    _check(
        "maf_enabled_roles() parses + normalises the env flag",
        roles == frozenset({"backend", "frontend"}),
        f"parsed={sorted(roles)}",
    )

    # ── Check 5: run_maf_agent raises MAFUnavailable when package absent ──────
    raised = False
    try:
        await run_maf_agent({"name": "BACKEND"}, ContinuumState(request="x"), {}, None)
    except MAFUnavailable:
        raised = True
    except Exception as exc:  # noqa: BLE001
        raised = False
        print(f"      (unexpected: {type(exc).__name__}: {exc})")
    _check(
        "run_maf_agent() raises MAFUnavailable without the framework",
        raised,
        "MAFUnavailable raised",
    )

    # ── Check 6: fallback routing + offline invariant ────────────────────────
    # (a) Monkeypatch resolve_model → non-None sentinel and _run_llm → marker,
    #     so we can observe run_agent degrading MAF → LangChain when the package
    #     is absent but a model is "resolved".
    os.environ[maf_runner.MAF_ENV_FLAG] = "backend"
    orig_resolve = agent_runner.resolve_model
    orig_run_llm = agent_runner._run_llm
    fallback_marker = {"code": {"src/main.py": "# from langchain fallback\n"}}

    async def _fake_run_llm(model, spec, state, skills, ctx):  # noqa: ANN001
        return dict(fallback_marker)

    agent_runner.resolve_model = lambda spec: object()  # truthy, non-None
    agent_runner._run_llm = _fake_run_llm
    try:
        st = ContinuumState(request="add a backend endpoint")
        await agent_runner.run_agent(st, "backend")
        routed_to_llm = (st.code or {}).get("src/main.py", "").strip() == "# from langchain fallback"
    finally:
        agent_runner.resolve_model = orig_resolve
        agent_runner._run_llm = orig_run_llm
        os.environ.pop(maf_runner.MAF_ENV_FLAG, None)

    # (b) Pure offline backend run still yields code (model is None → offline path,
    #     MAF gating never engages).
    st_offline = ContinuumState(request="add a backend endpoint")
    await agent_runner.run_agent(st_offline, "backend")
    offline_has_code = bool(st_offline.code)

    _check(
        "MAF→LangChain fallback routes correctly; offline path untouched",
        routed_to_llm and offline_has_code,
        f"fallback_to_llm={routed_to_llm}, offline_code_files={len(st_offline.code or {})}",
    )

    # ── Summary ──────────────────────────────────────────────────────────────
    print()
    passed_n = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    print(f"{passed_n}/{total} checks passed")
    return 0 if passed_n == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
