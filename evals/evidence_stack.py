# File: continuum/evals/evidence_stack.py
"""
M6: Evidence Stack — a 6-layer, human-readable proof that a run is merge-ready.

`build_evidence_stack(state)` is a pure function of ContinuumState (no network,
no credentials) so it is offline-safe per the evals/scorers/deterministic.py
convention. Each layer maps onto the *real* gates recorded on the state:

  1. Build / compile        — local_verify GateStatus (ruff + mypy portion)
  2. Regression suite       — local_verify GateStatus (pytest portion)
  3. Acceptance-criteria    — contract_validate GateStatus
  4. Scope conformance      — M6 stub (pass); M7 wires this to mapping fidelity
  5. Lint + secret scan     — security_sast GateStatus
  6. Human review           — story/design/merge approval flags

Each entry is `{"layer", "status", "detail"}` where status is one of
"pass" / "fail" / "pending".
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Number of layers the stack always returns (consumers rely on this).
EVIDENCE_LAYER_COUNT = 6


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


def build_evidence_stack(state: Any) -> List[Dict[str, str]]:
    """
    Build the 6-layer Evidence Stack for a run. Pure, offline-safe.

    Always returns exactly EVIDENCE_LAYER_COUNT entries, even for a run that
    has not produced any gates yet (every layer then reads "pending").
    """
    local_verify = _gate(state, "local_verify")
    contract = _gate(state, "contract_validate")
    sast = _gate(state, "security_sast")

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

    return [
        {
            "layer": "Build / compile",
            "sublabel": "ruff + mypy + py_compile",
            "status": _gate_status(local_verify),
            "detail": _gate_detail(local_verify, "lint, types, and build clean"),
        },
        {
            "layer": "Regression suite",
            "sublabel": "pytest (end to end)",
            "status": _gate_status(local_verify),
            "detail": _gate_detail(local_verify, "test suite green"),
        },
        {
            "layer": "Acceptance-criteria check",
            "sublabel": "OpenAPI contract validation",
            "status": _gate_status(contract),
            "detail": _gate_detail(contract, "contract structurally valid"),
        },
        {
            "layer": "Scope conformance",
            "sublabel": "mapping fidelity (D11) — wired in M7",
            "status": "pass",
            "detail": "stub: no business mappings enforced yet (M6)",
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
