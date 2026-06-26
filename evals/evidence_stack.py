# File: continuum/evals/evidence_stack.py
"""
Evidence Stack — a 6-layer, human-readable proof that a run is merge-ready.

`build_evidence_stack(state)` is a pure function of ContinuumState (no network,
no credentials) so it is offline-safe per the evals/scorers/deterministic.py
convention. Each layer maps onto the *real* gates recorded on the state:

  1. Build / compile        — local_verify GateStatus (ruff + mypy portion)
  2. Regression suite       — local_verify GateStatus (pytest portion)
  3. Acceptance-criteria    — contract_validate GateStatus
  4. Scope conformance      — M7: state.mapping_fidelity.exact_match
                              (pass/skip when no business_mappings supplied)
  5. Lint + secret scan     — security_sast GateStatus
  6. Human review           — story/design/merge approval flags

Each entry is `{"layer", "status", "detail"}` where status is one of
"pass" / "fail" / "pending".
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Number of layers the stack always returns (consumers rely on this).
EVIDENCE_LAYER_COUNT = 6

# M10: which ASSERT spec backs each layer (by 1-based layer index → spec id).
# Used only when `build_evidence_stack` is called with an `asserts` mapping;
# absent that, the stack is identical to its pre-M10 shape.
_LAYER_SPEC = {
    1: "rule1_no_push_and_pray",
    2: "rule1_no_push_and_pray",
    3: "rule8_plans_are_contracts",
    4: "m7_scope_fidelity",
    5: "rule2_every_stage_gate",
    6: "rule9_governed_harness",
}


def _gate(state: Any, name: str) -> Optional[Any]:
    """Return the named GateStatus on the state, or None."""
    for g in getattr(state, "gates", []) or []:
        if getattr(g, "name", None) == name:
            return g
    return None


def _gate_status(gate: Optional[Any]) -> str:
    """Map a GateStatus.status ('green'/'red'/'pending'/None) onto pass/fail/pending."""
    if gate is None:
        return "pending"
    status = getattr(gate, "status", None)
    if status == "green":
        return "pass"
    if status == "red":
        return "fail"
    return "pending"


def _gate_detail(gate: Optional[Any], ok_text: str) -> str:
    """Human detail string for a gate layer."""
    if gate is None:
        return "not yet run"
    if getattr(gate, "status", None) == "green":
        return ok_text
    msg = getattr(gate, "error_message", None)
    return (msg or "failed")[:300]


def _combine_status(gates: List[Optional[Any]]) -> str:
    """Combine several gates (P1.1): any red → fail; all present green → pass; else pending."""
    present = [g for g in gates if g is not None]
    if not present:
        return "pending"
    statuses = [_gate_status(g) for g in present]
    if "fail" in statuses:
        return "fail"
    if all(s == "pass" for s in statuses):
        return "pass"
    return "pending"


def _combine_detail(named_gates: List[tuple], ok_text: str) -> str:
    """Human detail for a combined layer — names the failing sub-gate(s)."""
    failed = [name for name, g in named_gates
              if g is not None and getattr(g, "status", None) == "red"]
    if failed:
        return "failed: " + ", ".join(failed)
    present = [name for name, g in named_gates if g is not None]
    if not present:
        return "not yet run"
    return ok_text


def _exec_evidence(state: Any, step: str) -> tuple:
    """
    P0.2: extract real ExecResult evidence from Box Lite if present.
    Returns (status_str, stdout_snippet) or (None, None) to fall back to gate status.
    `step` is one of "lint", "typecheck", "test".
    """
    ev_store = getattr(state, "_exec_evidence", None)
    if ev_store is None:
        return None, None
    for role in ("database", "backend", "frontend", "security", "developer"):
        role_ev = ev_store.get(role, {})
        if step in role_ev:
            r = role_ev[step]
            status = "pass" if r.get("returncode") == 0 else "fail"
            snippet = (r.get("stdout") or "")[:300].strip()
            return status, snippet or f"rc={r.get('returncode')}"
    return None, None


def _scope_status(state: Any) -> str:
    """Layer-4 status driven by M7 state.mapping_fidelity."""
    bm = getattr(state, "business_mappings", []) or []
    if not bm:
        return "pass"  # nothing to enforce
    mf = getattr(state, "mapping_fidelity", None)
    if mf is None:
        return "pending"
    return "pass" if mf.get("exact_match") else "fail"


def _scope_detail(state: Any) -> str:
    """Layer-4 detail string driven by M7 state.mapping_fidelity."""
    bm = getattr(state, "business_mappings", []) or []
    if not bm:
        return "no business_mappings supplied (scope guard skipped)"
    mf = getattr(state, "mapping_fidelity", None)
    if mf is None:
        return "scope guard not yet run"
    if mf.get("exact_match"):
        n = len(mf.get("found", []))
        return f"{n} mapping(s) confirmed in generated code"
    parts: List[str] = []
    missing = mf.get("missing_in_code", [])
    extra = mf.get("extra_in_code", [])
    if missing:
        parts.append("missing: " + ", ".join(missing))
    if extra:
        parts.append("extra: " + ", ".join(extra))
    return "; ".join(parts) if parts else "scope mismatch"


def build_evidence_stack(
    state: Any,
    asserts: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, str]]:
    """
    Build the 6-layer Evidence Stack for a run. Pure, offline-safe.

    Always returns exactly EVIDENCE_LAYER_COUNT entries, even for a run that
    has not produced any gates yet (every layer then reads "pending").

    M10: when `asserts` is supplied (the `specs` mapping from
    `evals.assert_specs.evaluate_all(state)`), each layer is annotated with the
    machine-checked rubric verdict backing it (`assert_rule` + `assert_verdict`).
    Omitting `asserts` yields the exact pre-M10 shape (six layers, no extra keys)
    so existing callers and verifiers are unaffected.
    """
    local_verify = _gate(state, "local_verify")
    contract = _gate(state, "contract_validate")
    sast = _gate(state, "security_sast")

    # P1.1: layers 1 & 2 read INDEPENDENT gates (lint+typecheck vs test) when the
    # split gates are present, so "6 independent signals" is literally true
    # (resolves OQ-3). When only the composite `local_verify` exists (pre-P1.1
    # states), both layers fall back to it — preserving the original shape.
    lint = _gate(state, "lint")
    typecheck = _gate(state, "typecheck")
    test = _gate(state, "test")
    have_split = lint is not None or typecheck is not None or test is not None

    if have_split:
        build_status = _combine_status([lint, typecheck])
        build_detail = _combine_detail([("lint", lint), ("typecheck", typecheck)],
                                       "lint + types clean")
        regression_status = _gate_status(test)
        regression_detail = _gate_detail(test, "test suite green")
    else:
        build_status = _gate_status(local_verify)
        build_detail = _gate_detail(local_verify, "lint, types, and build clean")
        regression_status = _gate_status(local_verify)
        regression_detail = _gate_detail(local_verify, "test suite green")

    # P0.2: upgrade layers 1+2 with real BoxLite ExecResult when available.
    # Box Lite evidence takes priority over gate-status strings — it's real stdout.
    _ev_lint, _ev_lint_detail = _exec_evidence(state, "lint")
    if _ev_lint is not None:
        build_status, build_detail = _ev_lint, _ev_lint_detail

    _ev_test, _ev_test_detail = _exec_evidence(state, "test")
    if _ev_test is not None:
        regression_status, regression_detail = _ev_test, _ev_test_detail

    # Layer 6 — human review: combine the three approval flags.
    approvals = {
        "story": bool(getattr(state, "story_approved", False)),
        "design": bool(getattr(state, "design_approved", False)),
        "merge": bool(getattr(state, "merge_approved", False)),
    }
    approved = [k for k, v in approvals.items() if v]
    if len(approved) == 3:
        human_status, human_detail = "pass", "story, design, merge all approved"
    elif approved:
        human_status = "pending"
        human_detail = "approved: " + ", ".join(approved)
    else:
        human_status, human_detail = "pending", "awaiting human review"

    layers = [
        {
            "layer": "Build / compile",
            "sublabel": "lint + typecheck (independent)",
            "status": build_status,
            "detail": build_detail,
        },
        {
            "layer": "Regression suite",
            "sublabel": "pytest (independent ground truth)",
            "status": regression_status,
            "detail": regression_detail,
        },
        {
            "layer": "Acceptance-criteria check",
            "sublabel": "OpenAPI contract validation",
            "status": _gate_status(contract),
            "detail": _gate_detail(contract, "contract structurally valid"),
        },
        {
            "layer": "Scope conformance",
            "sublabel": "mapping fidelity (D11 Scope-Guard)",
            "status": _scope_status(state),
            "detail": _scope_detail(state),
        },
        {
            "layer": "Lint + secret scan",
            "sublabel": "independent ground truth (SAST)",
            "status": _gate_status(sast),
            "detail": _gate_detail(sast, "no secrets or SAST findings"),
        },
        {
            "layer": "Human review",
            "sublabel": "story / design / merge gates",
            "status": human_status,
            "detail": human_detail,
        },
    ]

    # M10: annotate each layer with the ASSERT rubric verdict backing it.
    if asserts:
        for idx, layer in enumerate(layers, start=1):
            spec = asserts.get(_LAYER_SPEC.get(idx, ""))
            if spec:
                layer["assert_rule"] = spec.get("rule", "")
                layer["assert_verdict"] = spec.get("verdict", "")

    return layers
