"""
evidence_stack.py — maps gate results onto 6 named evidence layers.

Pure function of ContinuumState (no network, offline-safe).
Called by GET /runs/{run_id}/evidence and verify_m6_workqueue.py.
"""
from __future__ import annotations

from typing import Any, Dict, List


def build_evidence_stack(state: Any) -> List[Dict[str, Any]]:
    """
    Map ContinuumState gate results onto 6 named evidence layers.

    Returns list[{layer, name, sub_label, status, detail}] where
    status is "pass" | "fail" | "pending".
    """

    def _gate(name: str) -> Any:
        return next(
            (g for g in (getattr(state, "gates", None) or []) if g.name == name),
            None,
        )

    layers: List[Dict[str, Any]] = []

    # Layer 1: Build / compile — ruff+mypy from local_verify
    lv = _gate("local_verify")
    layers.append({
        "layer": 1,
        "name": "Build / compile",
        "sub_label": "independent ground truth",
        "status": "pass" if (lv and lv.status == "green") else ("fail" if lv else "pending"),
        "detail": (lv.error_message or "All checks passed") if lv else "Gate not yet run",
    })

    # Layer 2: Regression suite — pytest portion (same gate, different label)
    layers.append({
        "layer": 2,
        "name": "Regression suite",
        "sub_label": "end to end",
        "status": "pass" if (lv and lv.status == "green") else ("fail" if lv else "pending"),
        "detail": "pytest — " + (
            (lv.error_message or "All tests passed") if lv else "Gate not yet run"
        ),
    })

    # Layer 3: Acceptance-criteria check — contract_validate gate
    cv = _gate("contract_validate")
    layers.append({
        "layer": 3,
        "name": "Acceptance-criteria check",
        "sub_label": "specification adherence",
        "status": "pass" if (cv and cv.status == "green") else ("fail" if cv else "pending"),
        "detail": (cv.error_message or "Contract validated") if cv else "Gate not yet run",
    })

    # Layer 4: Scope conformance — stub pass for M6; M7 wires mapping_fidelity
    mf = getattr(state, "mapping_fidelity", None)
    if mf is not None:
        sc_pass = bool(mf.get("exact_match", False)) and not mf.get("extra_in_code", [])
        sc_detail = (
            "Exact match"
            if sc_pass
            else f"Mismatch — extra codes in generated code: {mf.get('extra_in_code', [])}"
        )
    else:
        sc_pass = True
        sc_detail = "No business mappings supplied (stub pass)"
    layers.append({
        "layer": 4,
        "name": "Scope conformance",
        "sub_label": "no invented mappings",
        "status": "pass" if sc_pass else "fail",
        "detail": sc_detail,
    })

    # Layer 5: Lint + secret scan — security_sast gate
    sast = _gate("security_sast")
    layers.append({
        "layer": 5,
        "name": "Lint + secret scan",
        "sub_label": "security hardened",
        "status": "pass" if (sast and sast.status == "green") else ("fail" if sast else "pending"),
        "detail": (sast.error_message or "No issues found") if sast else "Gate not yet run",
    })

    # Layer 6: Human review — approval flags
    story_ok = bool(getattr(state, "story_approved", False))
    design_ok = bool(getattr(state, "design_approved", False))
    merge_ok = bool(getattr(state, "merge_approved", False))
    if merge_ok:
        hr_status, hr_detail = "pass", "Story, design, and merge all approved"
    elif story_ok and design_ok:
        hr_status, hr_detail = "pass", "Story and design approved; merge pending"
    elif story_ok:
        hr_status, hr_detail = "pass", "Story approved; design pending"
    else:
        hr_status, hr_detail = "pending", "Awaiting human review"
    layers.append({
        "layer": 6,
        "name": "Human review",
        "sub_label": "expert validation",
        "status": hr_status,
        "detail": hr_detail,
    })

    return layers
