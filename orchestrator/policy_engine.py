"""
policy_engine.py — M13 deterministic transition policy.

Policies, not agents, decide whether an artifact may advance. `can_transition`
is a pure function of the artifact's current fields and the declared state machine
(`orchestrator/state_machine.py`) — no I/O, no credentials, offline-safe.

    can_transition(artifact, from_state, to_state) -> (ok: bool, reason: str)

It checks, in order:
  1. the edge exists (to_state ∈ forward ∪ returns of from_state);
  2. the governing human gate (G1–G4), if any, is approved;
  3. the target state's required artifacts are present;
  4. the target state's quality gates are green.

`advance(artifact, to_state)` applies a transition by mutating
`artifact.lifecycle_state` when the policy permits it.
"""
from __future__ import annotations

from typing import Any, Tuple

from orchestrator.state_machine import (
    GATE_APPROVAL_FIELD,
    GATE_INFO,
    SDLCState,
    allowed_transitions,
    definition,
    gate_for_transition,
)


def _gate_green(artifact: Any, name: str) -> bool:
    for g in getattr(artifact, "gates", []) or []:
        if getattr(g, "name", None) == name:
            return getattr(g, "status", None) == "green"
    return False


def can_transition(
    artifact: Any, from_state: Any, to_state: Any
) -> Tuple[bool, str]:
    """Return (allowed, reason). `reason` explains a block or confirms the move."""
    src = SDLCState(from_state)
    dst = SDLCState(to_state)

    # 1. The edge must be declared.
    if dst not in allowed_transitions(src):
        allowed = ", ".join(s.value for s in allowed_transitions(src)) or "(none)"
        return False, f"transition not allowed: {src.value} → {dst.value} (allowed: {allowed})"

    # 2. Governing human gate (G1–G4), if any.
    gate = gate_for_transition(src, dst)
    if gate is not None:
        field = GATE_APPROVAL_FIELD[gate]
        if not bool(getattr(artifact, field, False)):
            label = GATE_INFO[gate]["name"]
            return False, f"awaiting {gate.value} ({label}) — {field} not granted"

    # 3. Target state's required artifacts.
    dst_def = definition(dst)
    for art in dst_def.required_artifacts:
        if not getattr(artifact, art, None):
            return False, f"missing required artifact for {dst.value}: {art}"

    # 4. Target state's quality gates.
    for qg in dst_def.quality_gates:
        if not _gate_green(artifact, qg):
            return False, f"quality gate not green for {dst.value}: {qg}"

    return True, f"ok: {src.value} → {dst.value}"


def advance(artifact: Any, to_state: Any) -> Tuple[bool, str]:
    """
    Attempt to move `artifact` from its current `lifecycle_state` to `to_state`.
    On success, mutate `artifact.lifecycle_state` and return (True, reason).
    On a policy block, leave the artifact unchanged and return (False, reason).
    """
    current = getattr(artifact, "lifecycle_state", SDLCState.NEW)
    ok, reason = can_transition(artifact, current, to_state)
    if ok:
        artifact.lifecycle_state = SDLCState(to_state)
    return ok, reason
