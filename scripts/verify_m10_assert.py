#!/usr/bin/env python
"""
verify_m10_assert.py — M10 ASSERT / Rubric Eval Integration.

Proves the rubric layer is declarative, machine-checkable, and offline-safe:

  1. The ASSERT registry covers Rules 1–9 + the M7 scope invariant.
  2. A merge-ready state passes every applicable trial spec (rubric value = 1.0).
  3. A broken state fails exactly the rules it violates (push-and-pray, ungated
     artifact, blown retry budget) — the specs discriminate.
  4. The M7 scope spec is three-valued: pass / fail / na driven by
     mapping_fidelity + business_mappings.
  5. judge.score() is now ASSERT-driven (method="assert"), keeps its public
     contract (value/reason/needs_human_review), and exposes per-spec verdicts.
  6. Governance specs grade the harness; the Evidence Stack annotates layers with
     rubric verdicts; and OTel export is a dormant no-op offline (events still
     flow with the package absent / flag unset).

Usage:
  python scripts/verify_m10_assert.py
Expected output:
  6/6 checks passed
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from evals import assert_specs  # noqa: E402
from evals.evidence_stack import build_evidence_stack  # noqa: E402
from evals.scorers import judge  # noqa: E402
from orchestrator import events  # noqa: E402
from orchestrator.agent_runner import AgentContext, run_agent  # noqa: E402
from orchestrator.state import ContinuumState, GateStatus  # noqa: E402

CHECKS: list = []


def _check(name: str, passed: bool, detail: str = "") -> None:
    icon = "PASS" if passed else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"  [{icon}] {name}{suffix}")
    CHECKS.append((name, passed, detail))


def _merge_ready_state() -> ContinuumState:
    """A clean, fully-gated run state that should satisfy every trial spec."""
    st = ContinuumState(request="Add a user profile endpoint")
    st.story = {
        "title": "Add a user profile endpoint to the API",
        "description": "Expose GET/PUT /users/{id} so clients can read and update profiles.",
        "acceptance_criteria": ["GET returns 200", "PUT validates input", "404 on missing"],
    }
    st.contract = "openapi: 3.0.0\npaths:\n  /users/{id}:\n    get: {}\n    put: {}\n"
    st.dag = {"tasks": [{"id": "t1", "name": "build endpoint"}]}
    st.code = {"src/main.py": "# app\n", "src/models.py": "# models\n"}
    st.gates = [
        GateStatus(name="contract_validate", status="green"),
        GateStatus(name="local_verify", status="green"),
        GateStatus(name="security_sast", status="green"),
    ]
    return st


def _broken_state() -> ContinuumState:
    """A run that violates Rules 1, 2, and 4 (but keeps a well-formed plan)."""
    st = ContinuumState(request="Add a user profile endpoint")
    st.story = {"title": "Add user profile", "description": "Some described feature here."}
    st.contract = "openapi: 3.0.0\npaths:\n  /users:\n    get: {}\n"  # produced…
    st.dag = {"tasks": [{"id": "t1"}]}
    st.code = {"src/main.py": "# app\n"}
    # local_verify RED and over the retry budget; contract_validate MISSING.
    st.gates = [GateStatus(name="local_verify", status="red", retry_count=5, error_message="boom")]
    return st


async def main() -> int:
    print("M10 ASSERT / Rubric Eval verification")
    print("=" * 50)

    # ── Check 1: registry coverage ───────────────────────────────────────────
    rule_ids = assert_specs.all_rule_ids()
    expected = {f"Rule {n}" for n in range(1, 10)} | {"M7 scope"}
    covered = expected.issubset(set(rule_ids))
    _check(
        "ASSERT registry covers Rules 1–9 + M7 scope",
        covered and len(assert_specs.ALL_SPECS) >= 10,
        f"{len(assert_specs.ALL_SPECS)} specs, rules={sorted(rule_ids)}",
    )

    # ── Check 2: merge-ready state passes all trial specs ─────────────────────
    res_ok = assert_specs.evaluate_specs(_merge_ready_state())
    _check(
        "merge-ready state passes every applicable trial spec",
        res_ok["value"] == 1.0 and res_ok["n_fail"] == 0,
        f"value={res_ok['value']}, pass={res_ok['n_pass']}, fail={res_ok['n_fail']}, na={res_ok['n_na']}",
    )

    # ── Check 3: broken state fails exactly the violated rules ────────────────
    res_bad = assert_specs.evaluate_specs(_broken_state())
    sp = res_bad["specs"]
    fails_right = (
        sp["rule1_no_push_and_pray"]["verdict"] == "fail"
        and sp["rule2_every_stage_gate"]["verdict"] == "fail"
        and sp["rule4_bounded_autofix"]["verdict"] == "fail"
        and sp["rule8_plans_are_contracts"]["verdict"] == "pass"  # plan stays well-formed
    )
    _check(
        "broken state fails Rules 1/2/4, leaves Rule 8 green (specs discriminate)",
        fails_right and res_bad["value"] < 1.0,
        f"value={res_bad['value']}, fails={[k for k, v in sp.items() if v['verdict'] == 'fail']}",
    )

    # ── Check 4: M7 scope spec is three-valued ────────────────────────────────
    st_pass = _merge_ready_state()
    st_pass.business_mappings = [{"code": "BR", "label": "Branch"}]
    st_pass.mapping_fidelity = {"exact_match": True, "found": ["BR"]}
    v_pass = assert_specs.evaluate_specs(st_pass)["specs"]["m7_scope_fidelity"]["verdict"]

    st_fail = _merge_ready_state()
    st_fail.business_mappings = [{"code": "BR", "label": "Branch"}]
    st_fail.mapping_fidelity = {"exact_match": False, "extra_in_code": ["EXTRA"]}
    v_fail = assert_specs.evaluate_specs(st_fail)["specs"]["m7_scope_fidelity"]["verdict"]

    v_na = assert_specs.evaluate_specs(_merge_ready_state())["specs"]["m7_scope_fidelity"]["verdict"]
    _check(
        "M7 scope spec → pass / fail / na as expected",
        v_pass == "pass" and v_fail == "fail" and v_na == "na",
        f"exact={v_pass}, mismatch={v_fail}, no_mappings={v_na}",
    )

    # ── Check 5: judge.score() is ASSERT-driven + contract preserved ──────────
    ctx = AgentContext(repo_path=".")
    st_run = ContinuumState(request="Build a CRUD endpoint for tasks")
    for role in ("bsa", "architect", "planner", "developer", "security"):
        await run_agent(st_run, role, ctx)
    jr = await judge.score(st_run, {})
    contract_ok = (
        jr.get("method", "").startswith("assert")
        and "specs" in jr
        and isinstance(jr.get("value"), float)
        and 0.0 <= jr["value"] <= 1.0
        and "needs_human_review" in jr
        and "reason" in jr
    )
    _check(
        "judge.score() is ASSERT-driven and keeps its public contract",
        contract_ok,
        f"method={jr.get('method')}, value={jr.get('value')}, specs={len(jr.get('specs', {}))}",
    )

    # ── Check 6: governance specs + evidence annotation + OTel no-op ──────────
    gov = assert_specs.evaluate_governance()
    gov_specs = gov["specs"]
    gov_ok = (
        gov_specs["rule3_local_equals_ci"]["verdict"] == "pass"
        and gov_specs["rule5_skills_are_atoms"]["verdict"] == "pass"
        and gov_specs["rule9_governed_harness"]["verdict"] == "pass"
    )

    # Evidence Stack annotation: with asserts → verdicts attached; without → not.
    all_specs = assert_specs.evaluate_all(st_run)["specs"]
    stack_annotated = build_evidence_stack(st_run, asserts=all_specs)
    stack_plain = build_evidence_stack(st_run)
    annotate_ok = (
        len(stack_annotated) == 6
        and len(stack_plain) == 6
        and all("assert_verdict" in layer for layer in stack_annotated)
        and all("assert_verdict" not in layer for layer in stack_plain)
    )

    # OTel: dormant no-op when the flag is unset AND when set without the package.
    events._reset_otel_cache()
    os.environ.pop(events._OTEL_ENV_FLAG, None)
    otel_off = events.otel_enabled() is False
    await events.event_bus.emit("m10-test", {"event_type": "agent_start", "agent": "bsa"})

    events._reset_otel_cache()
    os.environ[events._OTEL_ENV_FLAG] = "1"
    # opentelemetry is absent in the thin env → still disabled, emit still safe.
    otel_flag_no_pkg = events.otel_enabled() is False
    await events.event_bus.emit("m10-test", {"event_type": "gate_green", "agent": "developer", "gate": "local_verify"})
    os.environ.pop(events._OTEL_ENV_FLAG, None)
    events._reset_otel_cache()

    history = events.event_bus.run_history("m10-test")
    events.event_bus.purge("m10-test")

    _check(
        "governance specs pass; evidence annotated; OTel is a dormant no-op",
        gov_ok and annotate_ok and otel_off and otel_flag_no_pkg and len(history) == 2,
        f"gov_ok={gov_ok}, annotate_ok={annotate_ok}, otel_off={otel_off}, "
        f"otel_no_pkg={otel_flag_no_pkg}, events_flowed={len(history)}",
    )

    # ── Summary ──────────────────────────────────────────────────────────────
    print()
    passed_n = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    print(f"{passed_n}/{total} checks passed")
    return 0 if passed_n == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
