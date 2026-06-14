#!/usr/bin/env python
"""
verify_m12_compliance.py — M12 Compliance Report offline verification.

Three run shapes each produce a valid, structured compliance artifact:

  1. A complete (merge-ready) run — all 8 sections present, the Evidence Stack has
     its 6 layers, lead time is computed, the audit trail is packaged, and the
     assertions checklist passes (all gates green, scope guard ok).
  2. A blocked run (scope_conformance red) — still a valid 8-section report, but
     the assertions honestly record scope_guard_passed=False / all_gates_green=False.
  3. A returned run with missing data — the spec, business mappings, audit trail
     and lead time are absent, and each is rendered as an explicit null *with a
     reason* enumerated in report["missing"] — never silently omitted.

Usage:
  python scripts/verify_m12_compliance.py
Expected output:
  3/3 checks passed
"""
from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.compliance import SECTION_ORDER, build_compliance_report, render_compliance_html  # noqa: E402
from orchestrator.state import ContinuumState, GateStatus  # noqa: E402

CHECKS: list = []

_SPEC = {
    "overview": "Branch management",
    "api_endpoints": ["GET /branches", "POST /branches"],
    "data_entities": ["Branch"],
    "out_of_scope": ["Mobile app"],
}


def _check(name: str, passed: bool, detail: str = "") -> None:
    icon = "PASS" if passed else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"  [{icon}] {name}{suffix}")
    CHECKS.append((name, passed, detail))


def _events(run_id: str) -> list:
    return [
        {"event_type": "agent_start", "agent": "bsa", "run_id": run_id, "timestamp": 100.0, "data": {}},
        {"event_type": "gate_green", "agent": "developer", "run_id": run_id, "timestamp": 101.0,
         "data": {"gate_name": "local_verify"}},
        {"event_type": "run_complete", "agent": "", "run_id": run_id, "timestamp": 102.0, "data": {}},
    ]


def _sections_present(report: dict) -> bool:
    return set(report["sections"].keys()) == set(SECTION_ORDER) and report["section_order"] == SECTION_ORDER


async def main() -> int:
    print("M12 Compliance Report verification")
    print("=" * 50)

    # ── Check 1: complete run → valid report, assertions pass ────────────────
    st = ContinuumState(request="Build a Branch Management API")
    st.run_id = "run-complete"
    st.component = "branch-management"
    st.story = {"title": "Branch management", "spec": _SPEC}
    st.business_mappings = [{"code": "BR", "label": "Branch"}]
    st.mapping_fidelity = {"supplied": ["BR"], "found": ["BR"], "extra_in_code": [],
                           "missing_in_code": [], "exact_match": True}
    st.gates = [
        GateStatus(name="local_verify", status="green"),
        GateStatus(name="contract_validate", status="green"),
        GateStatus(name="scope_conformance", status="green"),
        GateStatus(name="security_sast", status="green"),
    ]
    st.story_approved = st.design_approved = st.merge_approved = True
    st.run_status = "done"
    st.started_at, st.completed_at = 100.0, 142.5
    st.cost_usd = 0.06

    rep = build_compliance_report("run-complete", st, events=_events("run-complete"))
    secs = rep["sections"]
    html_out = render_compliance_html(rep)
    ok1 = (
        _sections_present(rep)
        and secs["evidence_stack"]["layer_count"] == 6
        and len(secs["evidence_stack"]["layers"]) == 6
        and secs["run_metadata"]["lead_time_s"] == 42.5
        and secs["audit_trail"]["count"] == 3
        and secs["compliance_assertions"]["passed"] is True
        and secs["compliance_assertions"]["checks"]["all_gates_green"] is True
        and secs["spec"]["source"] == "spec_registry"
        and rep["complete"] is True
        and "run-complete" in html_out
    )
    _check(
        "complete run → 8 sections, 6 evidence layers, assertions pass, HTML renders",
        ok1,
        f"complete={rep['complete']}, lead_time={secs['run_metadata']['lead_time_s']}, "
        f"passed={secs['compliance_assertions']['passed']}",
    )

    # ── Check 2: blocked run → valid report, honest failing assertions ───────
    stb = ContinuumState(request="Build a Branch Management API")
    stb.run_id = "run-blocked"
    stb.story = {"spec": _SPEC}
    stb.business_mappings = [{"code": "BR", "label": "Branch"}]
    stb.mapping_fidelity = {"supplied": ["BR"], "found": ["BR"], "extra_in_code": ["EXTRA"],
                            "missing_in_code": [], "exact_match": False}
    stb.gates = [
        GateStatus(name="local_verify", status="green"),
        GateStatus(name="scope_conformance", status="red", error_message="extra: EXTRA"),
    ]
    stb.run_status = "blocked"
    stb.started_at, stb.completed_at = 200.0, 210.0

    repb = build_compliance_report("run-blocked", stb, events=_events("run-blocked"))
    cb = repb["sections"]["compliance_assertions"]["checks"]
    scope_layer = repb["sections"]["evidence_stack"]["layers"][3]  # layer 4 (scope)
    ok2 = (
        _sections_present(repb)
        and cb["scope_guard_passed"] is False
        and cb["all_gates_green"] is False
        and repb["sections"]["compliance_assertions"]["passed"] is False
        and scope_layer["status"] == "fail"
        and repb["sections"]["business_mappings"]["conformance"]["exact_match"] is False
    )
    _check(
        "blocked run → valid report; assertions record scope/gate failure honestly",
        ok2,
        f"scope_passed={cb['scope_guard_passed']}, all_green={cb['all_gates_green']}",
    )

    # ── Check 3: returned run with missing data → explicit null + reasons ────
    str_ = ContinuumState(request="Add something vague")
    str_.run_id = "run-returned"
    str_.story = {"title": "vague"}            # no spec
    str_.business_mappings = []                # none supplied
    str_.gates = [GateStatus(name="local_verify", status="green")]
    str_.run_status = "returned"
    str_.reject_reason = "Story unclear — needs acceptance criteria"
    # not completed → no lead time

    repr_ = build_compliance_report("run-returned", str_, events=[])  # no audit trail
    sr = repr_["sections"]
    missing_sections = {m["section"] for m in repr_["missing"]}
    ok3 = (
        _sections_present(repr_)
        and sr["spec"]["spec"] is None and "_missing_reason" in sr["spec"]
        and "_missing_reason" in sr["business_mappings"]
        and sr["audit_trail"]["count"] == 0 and "_missing_reason" in sr["audit_trail"]
        and sr["run_metadata"]["lead_time_s"] is None
        and sr["run_metadata"]["reject_reason"] == "Story unclear — needs acceptance criteria"
        and sr["run_metadata"]["run_status"] == "returned"
        and {"spec", "business_mappings", "audit_trail", "run_metadata"}.issubset(missing_sections)
        and repr_["complete"] is False
        and all(m.get("reason") for m in repr_["missing"])  # every missing item HAS a reason
    )
    _check(
        "returned run → missing data is explicit null + reason (not omitted)",
        ok3,
        f"missing={sorted(missing_sections)}",
    )

    # ── Summary ──────────────────────────────────────────────────────────────
    print()
    passed_n = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    print(f"{passed_n}/{total} checks passed")
    return 0 if passed_n == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
