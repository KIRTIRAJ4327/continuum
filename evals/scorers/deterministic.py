"""
Deterministic scorers (~60% weight in the eval mix).

Each scorer takes a ContinuumState + label dict and returns a float in [0, 1].
All scorers must work offline — they check structural properties of state,
never make LLM calls.

Scorers map directly onto existing pipeline gates so there is no drift between
what the gate enforces at runtime and what the eval measures.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from orchestrator.state import ContinuumState


# --------------------------------------------------------------------------- #
# Individual scorers
# --------------------------------------------------------------------------- #

def contract_valid(state: ContinuumState, label: Dict[str, Any]) -> Tuple[float, str]:
    """
    Passes when the contract_validate gate is green OR the contract string
    contains at least one path block.  Reuses the gate result when available.
    """
    gate = next((g for g in state.gates if g.name == "contract_validate"), None)
    if gate is not None:
        if gate.status == "green":
            return 1.0, "gate=green"
        if gate.status == "red":
            return 0.0, f"gate=red: {(gate.error_message or '')[:120]}"

    # Gate wasn't run (offline path) — check structural content.
    contract = state.contract or ""
    if not contract:
        return 0.0, "no contract produced"

    # Must have at least one path definition.
    has_path = bool(re.search(r"^\s{0,4}/\w", contract, re.MULTILINE))
    if not has_path:
        return 0.0, "contract has no path definitions"

    # Check label-required paths if specified.
    required_paths: List[str] = (label.get("contract") or {}).get("must_have_paths", [])
    for path in required_paths:
        # Loose match: the path stem appears anywhere in the contract text.
        stem = path.strip("/").split("/")[0]
        if stem not in contract.lower():
            return 0.5, f"contract missing path stem '{stem}'"

    return 1.0, "contract present with paths"


def sast_clean(state: ContinuumState, label: Dict[str, Any]) -> Tuple[float, str]:
    """
    Passes when the security_sast gate is green.
    Falls back to True when gate was not run (offline path has no SAST binary).
    """
    gate = next((g for g in state.gates if g.name == "security_sast"), None)
    if gate is None:
        return 1.0, "gate not run (offline) — assumed clean"
    if gate.status == "green":
        return 1.0, "sast=green"
    return 0.0, f"sast=red: {(gate.error_message or '')[:120]}"


def story_has_ac(state: ContinuumState, label: Dict[str, Any]) -> Tuple[float, str]:
    """
    Passes when the story has a non-empty acceptance_criteria list meeting the
    label's minimum count.
    """
    story = state.story or {}
    if not story.get("title"):
        return 0.0, "story missing title"

    ac = story.get("acceptance_criteria") or []
    if not isinstance(ac, list):
        ac = []

    min_ac: int = (label.get("story") or {}).get("min_acceptance_criteria", 1)
    if len(ac) >= min_ac:
        return 1.0, f"story has {len(ac)} AC (min={min_ac})"

    # Partial credit: at least the story exists even if AC count is low.
    if story.get("title"):
        return 0.5, f"story title present but only {len(ac)}/{min_ac} AC"

    return 0.0, "story missing AC"


def dag_has_tasks(state: ContinuumState, label: Dict[str, Any]) -> Tuple[float, str]:
    """Passes when the DAG has at least one task node."""
    dag = state.dag or {}
    tasks = dag.get("tasks") or []
    if not isinstance(tasks, list):
        tasks = []
    if tasks:
        return 1.0, f"dag has {len(tasks)} task(s)"
    # Planner plan also counts.
    plan = dag.get("plan")
    if plan:
        return 0.8, "dag has plan but no explicit tasks"
    return 0.0, "dag empty"


def code_has_files(state: ContinuumState, label: Dict[str, Any]) -> Tuple[float, str]:
    """Passes when state.code has at least min_files entries."""
    files = state.code or {}
    n = len(files)
    min_files: int = (label.get("code") or {}).get("min_files", 3)
    if n >= min_files:
        return 1.0, f"code has {n} file(s) (min={min_files})"
    if n > 0:
        return n / min_files, f"code has {n}/{min_files} file(s)"
    return 0.0, "no code files produced"


def schema_has_tables(state: ContinuumState, label: Dict[str, Any]) -> Tuple[float, str]:
    """
    Passes when the schema DDL mentions the tables required by the label.
    Lenient: checks for table name as a substring of the schema string.
    """
    schema = (state.schema or "").lower()
    if not schema:
        return 0.0, "no schema produced"

    required: List[str] = (label.get("schema") or {}).get("must_have_tables", [])
    if not required:
        return 1.0, "no table requirements in label"

    found = [t for t in required if t.lower() in schema]
    ratio = len(found) / len(required)
    missing = [t for t in required if t not in found]
    if ratio == 1.0:
        return 1.0, f"schema has all required tables: {required}"
    if ratio > 0:
        return ratio, f"schema missing tables: {missing}"
    return 0.0, f"schema missing all required tables: {required}"


# --------------------------------------------------------------------------- #
# Aggregator
# --------------------------------------------------------------------------- #

#: Weights sum to 1.0 within the deterministic scorer set.
SCORERS: Dict[str, Any] = {
    "contract_valid":   (contract_valid,   0.25),
    "sast_clean":       (sast_clean,       0.20),
    "story_has_ac":     (story_has_ac,     0.20),
    "dag_has_tasks":    (dag_has_tasks,    0.15),
    "code_has_files":   (code_has_files,   0.10),
    "schema_has_tables": (schema_has_tables, 0.10),
}


def score(
    state: ContinuumState,
    label: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Run all deterministic scorers and return a results dict.

    Returns:
        {
            "scores": {name: {"value": float, "reason": str}},
            "weighted": float,   # weighted average in [0, 1]
            "pass": bool,        # True when weighted >= 0.8
        }
    """
    results: Dict[str, Dict[str, Any]] = {}
    weighted_sum = 0.0
    total_weight = 0.0

    for name, (fn, weight) in SCORERS.items():
        try:
            value, reason = fn(state, label)
        except Exception as exc:  # noqa: BLE001
            value, reason = 0.0, f"scorer error: {exc}"
        results[name] = {"value": round(float(value), 4), "reason": reason}
        weighted_sum += float(value) * weight
        total_weight += weight

    weighted = weighted_sum / total_weight if total_weight else 0.0
    return {
        "scores": results,
        "weighted": round(weighted, 4),
        "pass": weighted >= 0.8,
    }
