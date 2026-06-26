"""
state_machine.py — M13 15-state SDLC lifecycle (the artifact's system of record).

The Control Tower model: an artifact moves through an explicit, policy-governed
lifecycle. This module is the *declaration* of that lifecycle — pure data, no I/O,
no imports from the rest of the orchestrator (so `orchestrator/state.py` can import
`SDLCState` without a cycle). The *enforcement* lives in `orchestrator/policy_engine.py`.

The 15 states (forward spine):

    NEW → EPIC_APPROVED → STORIES_READY → ARCH_READY → IMPL_READY
        → IN_PROGRESS → CODE_COMPLETE → TESTING → TESTS_PASSED
        → SECURITY_REVIEW → SECURITY_APPROVED → RELEASE_READY
        → DEPLOYED → IN_PRODUCTION → CLOSED

Four named human gates govern specific transitions (not every step):

    G1 Business      NEW            → EPIC_APPROVED
    G2 Architecture  ARCH_READY     → IMPL_READY
    G3 Release       RELEASE_READY  → DEPLOYED
    G4 Critical Inc. IN_PRODUCTION  → IN_PROGRESS   (incident / RCA return)

Each state declares its *entry criteria* (required artifacts + quality gates that
must be green to enter) and its allowed *forward* and *return* transitions. The
return edges make exception paths first-class (tests fail, security findings, prod
incident) rather than ad-hoc.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Tuple


class SDLCState(str, Enum):
    NEW = "new"
    EPIC_APPROVED = "epic_approved"
    STORIES_READY = "stories_ready"
    ARCH_READY = "arch_ready"
    IMPL_READY = "impl_ready"
    IN_PROGRESS = "in_progress"
    CODE_COMPLETE = "code_complete"
    TESTING = "testing"
    TESTS_PASSED = "tests_passed"
    SECURITY_REVIEW = "security_review"
    SECURITY_APPROVED = "security_approved"
    RELEASE_READY = "release_ready"
    DEPLOYED = "deployed"
    IN_PRODUCTION = "in_production"
    CLOSED = "closed"


class Gate(str, Enum):
    """The four named human approval gates (HITL)."""
    G1 = "G1"  # Business Approval
    G2 = "G2"  # Architecture Approval
    G3 = "G3"  # Release Approval
    G4 = "G4"  # Critical Incident Approval


# Human-readable gate metadata.
GATE_INFO: Dict[Gate, Dict[str, str]] = {
    Gate.G1: {"name": "Business Approval", "approval_field": "story_approved"},
    Gate.G2: {"name": "Architecture Approval", "approval_field": "design_approved"},
    Gate.G3: {"name": "Release Approval", "approval_field": "merge_approved"},
    Gate.G4: {"name": "Critical Incident Approval", "approval_field": "incident_approved"},
}

# Which artifact field grants each gate (read off ContinuumState via getattr).
GATE_APPROVAL_FIELD: Dict[Gate, str] = {g: info["approval_field"] for g, info in GATE_INFO.items()}


@dataclass(frozen=True)
class StateDefinition:
    """Entry criteria + allowed transitions for one SDLC state."""
    state: SDLCState
    description: str
    required_artifacts: Tuple[str, ...] = ()   # ContinuumState fields that must be truthy to ENTER
    quality_gates: Tuple[str, ...] = ()         # GateStatus names that must be green to ENTER
    forward: Tuple[SDLCState, ...] = ()         # allowed forward transitions FROM this state
    returns: Tuple[SDLCState, ...] = ()         # allowed return/exception transitions FROM this state
    sla_hours: Optional[int] = None


# ── The 15-state machine ────────────────────────────────────────────────────────
STATE_MACHINE: Dict[SDLCState, StateDefinition] = {
    SDLCState.NEW: StateDefinition(
        SDLCState.NEW, "Intent received; awaiting business approval.",
        forward=(SDLCState.EPIC_APPROVED,), sla_hours=24,
    ),
    SDLCState.EPIC_APPROVED: StateDefinition(
        SDLCState.EPIC_APPROVED, "Epic approved by the business (G1).",
        forward=(SDLCState.STORIES_READY,), sla_hours=24,
    ),
    SDLCState.STORIES_READY: StateDefinition(
        SDLCState.STORIES_READY, "BSA produced the story/spec.",
        required_artifacts=("story",), forward=(SDLCState.ARCH_READY,),
    ),
    SDLCState.ARCH_READY: StateDefinition(
        SDLCState.ARCH_READY, "Architecture (contract/schema) produced.",
        required_artifacts=("contract",), forward=(SDLCState.IMPL_READY,),
    ),
    SDLCState.IMPL_READY: StateDefinition(
        SDLCState.IMPL_READY, "Architecture approved (G2); ready to plan/build.",
        forward=(SDLCState.IN_PROGRESS,),
    ),
    SDLCState.IN_PROGRESS: StateDefinition(
        SDLCState.IN_PROGRESS, "Implementation underway (plan/DAG exists).",
        required_artifacts=("dag",), forward=(SDLCState.CODE_COMPLETE,),
    ),
    SDLCState.CODE_COMPLETE: StateDefinition(
        SDLCState.CODE_COMPLETE, "Code produced by the developer chain.",
        required_artifacts=("code",), forward=(SDLCState.TESTING,),
    ),
    SDLCState.TESTING: StateDefinition(
        SDLCState.TESTING, "Local verification running.",
        forward=(SDLCState.TESTS_PASSED,), returns=(SDLCState.CODE_COMPLETE,),
    ),
    SDLCState.TESTS_PASSED: StateDefinition(
        SDLCState.TESTS_PASSED, "Lint/type/test gate green.",
        quality_gates=("local_verify",),
        forward=(SDLCState.SECURITY_REVIEW,), returns=(SDLCState.CODE_COMPLETE,),
    ),
    SDLCState.SECURITY_REVIEW: StateDefinition(
        SDLCState.SECURITY_REVIEW, "Security agent reviewing.",
        forward=(SDLCState.SECURITY_APPROVED,), returns=(SDLCState.IN_PROGRESS,),
    ),
    SDLCState.SECURITY_APPROVED: StateDefinition(
        SDLCState.SECURITY_APPROVED, "SAST gate green; security signed off.",
        quality_gates=("security_sast",),
        forward=(SDLCState.RELEASE_READY,), returns=(SDLCState.IN_PROGRESS,),
    ),
    SDLCState.RELEASE_READY: StateDefinition(
        SDLCState.RELEASE_READY, "All gates green; awaiting release approval.",
        forward=(SDLCState.DEPLOYED,),
    ),
    SDLCState.DEPLOYED: StateDefinition(
        SDLCState.DEPLOYED, "Release approved (G3); deployed to SIT/UAT.",
        forward=(SDLCState.IN_PRODUCTION,),
    ),
    SDLCState.IN_PRODUCTION: StateDefinition(
        SDLCState.IN_PRODUCTION, "Live in production.",
        forward=(SDLCState.CLOSED,), returns=(SDLCState.IN_PROGRESS,),
    ),
    SDLCState.CLOSED: StateDefinition(
        SDLCState.CLOSED, "Work item closed (terminal).",
    ),
}

# Transition-level human gates: (from, to) -> Gate.
TRANSITION_GATES: Dict[Tuple[SDLCState, SDLCState], Gate] = {
    (SDLCState.NEW, SDLCState.EPIC_APPROVED): Gate.G1,
    (SDLCState.ARCH_READY, SDLCState.IMPL_READY): Gate.G2,
    (SDLCState.RELEASE_READY, SDLCState.DEPLOYED): Gate.G3,
    (SDLCState.IN_PRODUCTION, SDLCState.IN_PROGRESS): Gate.G4,
}


# ── Introspection helpers ────────────────────────────────────────────────────────
def all_states() -> Tuple[SDLCState, ...]:
    """The 15 states in canonical forward order."""
    return tuple(STATE_MACHINE.keys())


def definition(state: SDLCState) -> StateDefinition:
    return STATE_MACHINE[SDLCState(state)]


def allowed_transitions(state: SDLCState) -> Tuple[SDLCState, ...]:
    """Forward + return transitions allowed from `state`."""
    d = definition(state)
    return d.forward + d.returns


def gate_for_transition(from_state: SDLCState, to_state: SDLCState) -> Optional[Gate]:
    """The human gate governing a transition, or None."""
    return TRANSITION_GATES.get((SDLCState(from_state), SDLCState(to_state)))


def forward_path() -> Tuple[SDLCState, ...]:
    """Walk the forward spine NEW → … → CLOSED (the happy path)."""
    path = [SDLCState.NEW]
    cur = SDLCState.NEW
    seen = {cur}
    while STATE_MACHINE[cur].forward:
        nxt = STATE_MACHINE[cur].forward[0]
        if nxt in seen:  # guard against cycles
            break
        path.append(nxt)
        seen.add(nxt)
        cur = nxt
    return tuple(path)


def derive_lifecycle_state(artifact) -> str:
    """
    Best-effort derivation of the furthest lifecycle state an artifact has reached,
    from its produced artifacts + gates + approvals + run_status. Read-only and
    offline — used to surface `lifecycle_state` in the API without gating the live
    pipeline. Returns the SDLCState value (a str).
    """
    def has(name: str) -> bool:
        return bool(getattr(artifact, name, None))

    def gate_green(name: str) -> bool:
        for g in getattr(artifact, "gates", []) or []:
            if getattr(g, "name", None) == name:
                return getattr(g, "status", None) == "green"
        return False

    run_status = getattr(artifact, "run_status", "running")
    state = SDLCState.NEW

    if has("story_approved"):
        state = SDLCState.EPIC_APPROVED
    if has("story"):
        state = SDLCState.STORIES_READY
    if has("contract"):
        state = SDLCState.ARCH_READY
    if has("design_approved"):
        state = SDLCState.IMPL_READY
    if has("dag"):
        state = SDLCState.IN_PROGRESS
    if has("code"):
        state = SDLCState.CODE_COMPLETE
    if gate_green("local_verify"):
        state = SDLCState.TESTS_PASSED
    if gate_green("security_sast"):
        state = SDLCState.SECURITY_APPROVED
    if has("merge_approved"):
        state = SDLCState.RELEASE_READY
    if run_status == "done":
        state = SDLCState.IN_PRODUCTION
    return state.value
