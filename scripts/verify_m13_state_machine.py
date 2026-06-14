#!/usr/bin/env python
"""
verify_m13_state_machine.py — M13 15-State SDLC Machine + Policy Engine.

Proves the lifecycle is an explicit, policy-governed graph (offline, deterministic):

  1. Exactly 15 states are declared and the forward spine connects NEW → CLOSED
     through every one of them.
  2. An artifact satisfying every entry criterion advances through all 15 states
     via the policy engine; its lifecycle_state ends at CLOSED.
  3. An undeclared transition (NEW → TESTING) is blocked with a clear reason.
  4. A transition into a state whose required artifact is absent is blocked, and
     permitted once the artifact exists.
  5. G1–G4 govern exactly the right transitions; the gated transition is blocked
     until the approval is granted.
  6. Return/exception edges are first-class (TESTING → CODE_COMPLETE; the G4
     incident return IN_PRODUCTION → IN_PROGRESS), and advance() mutates
     lifecycle_state on success while leaving it unchanged on a block.

Usage:
  python scripts/verify_m13_state_machine.py
Expected output:
  6/6 checks passed
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.policy_engine import advance, can_transition  # noqa: E402
from orchestrator.state import ContinuumState, GateStatus  # noqa: E402
from orchestrator.state_machine import (  # noqa: E402
    Gate,
    SDLCState,
    all_states,
    forward_path,
    gate_for_transition,
)

CHECKS: list = []


def _check(name: str, passed: bool, detail: str = "") -> None:
    icon = "PASS" if passed else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"  [{icon}] {name}{suffix}")
    CHECKS.append((name, passed, detail))


def _ready_artifact() -> ContinuumState:
    """An artifact that satisfies every entry criterion on the forward spine."""
    st = ContinuumState(request="Build a Branch Management API")
    st.story = {"title": "Branch management", "spec": {"overview": "x"}}
    st.contract = "openapi: 3.0.0"
    st.dag = {"tasks": [{"id": "t1"}]}
    st.code = {"src/main.py": "# app\n"}
    st.story_approved = True     # G1
    st.design_approved = True    # G2
    st.merge_approved = True     # G3
    st.gates = [
        GateStatus(name="local_verify", status="green"),
        GateStatus(name="security_sast", status="green"),
    ]
    return st


def main() -> int:
    print("M13 15-State SDLC Machine verification")
    print("=" * 50)

    # ── Check 1: 15 states + connected forward spine ─────────────────────────
    states = all_states()
    spine = forward_path()
    _check(
        "15 states declared; forward spine NEW → CLOSED covers all of them",
        len(states) == 15
        and len(spine) == 15
        and spine[0] == SDLCState.NEW
        and spine[-1] == SDLCState.CLOSED
        and set(spine) == set(states),
        f"{len(states)} states, spine_len={len(spine)}",
    )

    # ── Check 2: happy-path traversal through all 15 via the policy engine ────
    art = _ready_artifact()
    traversal_ok = art.lifecycle_state == SDLCState.NEW
    blocked_at = None
    for nxt in spine[1:]:
        ok, reason = advance(art, nxt)
        if not ok:
            traversal_ok = False
            blocked_at = (nxt.value, reason)
            break
    _check(
        "ready artifact advances through all 15 states → CLOSED",
        traversal_ok and art.lifecycle_state == SDLCState.CLOSED,
        f"final={art.lifecycle_state.value}" + (f", blocked_at={blocked_at}" if blocked_at else ""),
    )

    # ── Check 3: undeclared transition is blocked ────────────────────────────
    ok3, reason3 = can_transition(_ready_artifact(), SDLCState.NEW, SDLCState.TESTING)
    _check(
        "undeclared transition NEW → TESTING blocked with reason",
        (not ok3) and "not allowed" in reason3,
        reason3,
    )

    # ── Check 4: missing required artifact blocks, presence permits ──────────
    no_story = _ready_artifact()
    no_story.story = None
    ok4a, reason4a = can_transition(no_story, SDLCState.EPIC_APPROVED, SDLCState.STORIES_READY)
    with_story = _ready_artifact()
    ok4b, _ = can_transition(with_story, SDLCState.EPIC_APPROVED, SDLCState.STORIES_READY)
    _check(
        "missing artifact blocks (story); present permits",
        (not ok4a) and "story" in reason4a and ok4b,
        f"missing→{reason4a}",
    )

    # ── Check 5: G1–G4 govern the right transitions; gate blocks until granted ─
    gate_map_ok = (
        gate_for_transition(SDLCState.NEW, SDLCState.EPIC_APPROVED) == Gate.G1
        and gate_for_transition(SDLCState.ARCH_READY, SDLCState.IMPL_READY) == Gate.G2
        and gate_for_transition(SDLCState.RELEASE_READY, SDLCState.DEPLOYED) == Gate.G3
        and gate_for_transition(SDLCState.IN_PRODUCTION, SDLCState.IN_PROGRESS) == Gate.G4
    )
    ungated = _ready_artifact()
    ungated.story_approved = False  # revoke G1
    ok5a, reason5a = can_transition(ungated, SDLCState.NEW, SDLCState.EPIC_APPROVED)
    ungated.story_approved = True
    ok5b, _ = can_transition(ungated, SDLCState.NEW, SDLCState.EPIC_APPROVED)
    _check(
        "G1–G4 map to the right edges; G1 blocks NEW→EPIC_APPROVED until granted",
        gate_map_ok and (not ok5a) and "G1" in reason5a and ok5b,
        f"gate_map_ok={gate_map_ok}, blocked={reason5a}",
    )

    # ── Check 6: return edges + advance() mutation semantics ─────────────────
    ret = _ready_artifact()
    ok6a, _ = can_transition(ret, SDLCState.TESTING, SDLCState.CODE_COMPLETE)  # tests-failed return

    incident = _ready_artifact()
    ok6b_blocked, reason6b = can_transition(incident, SDLCState.IN_PRODUCTION, SDLCState.IN_PROGRESS)
    incident.incident_approved = True  # grant G4
    ok6b_ok, _ = can_transition(incident, SDLCState.IN_PRODUCTION, SDLCState.IN_PROGRESS)

    # advance() mutates on success, leaves unchanged on block
    mover = _ready_artifact()
    adv_ok, _ = advance(mover, SDLCState.EPIC_APPROVED)          # valid from NEW (G1 granted)
    bad_ok, _ = advance(mover, SDLCState.CLOSED)                 # invalid from EPIC_APPROVED
    _check(
        "return edges first-class (tests-fail + G4 incident); advance() mutation correct",
        ok6a
        and (not ok6b_blocked) and "G4" in reason6b and ok6b_ok
        and adv_ok and mover.lifecycle_state == SDLCState.EPIC_APPROVED
        and (not bad_ok),
        f"tests_return={ok6a}, g4_blocked={not ok6b_blocked}, g4_after={ok6b_ok}, "
        f"after_advance={mover.lifecycle_state.value}",
    )

    # ── Summary ──────────────────────────────────────────────────────────────
    print()
    passed_n = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    print(f"{passed_n}/{total} checks passed")
    return 0 if passed_n == total else 1


if __name__ == "__main__":
    sys.exit(main())
