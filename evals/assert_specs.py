# File: continuum/evals/assert_specs.py
"""
ASSERT specs — declarative, machine-checkable rubrics (M10).

This module replaces the bespoke 1–5 LLM "judge" with a registry of declarative
assertion specs, one (or more) per governance Rule (the 9 Rules from the README)
plus the M7 scope invariant. Each spec is a *pure, offline-safe* predicate over a
ContinuumState (or, for governance specs, over the harness itself). No spec makes
an LLM call, so the canonical offline path is byte-for-byte deterministic.

Two spec families:

  TRIAL_SPECS       — graded against a run's ContinuumState each trial. These
                      drive the rubric score that replaces the old judge value.
                      Rules: 1 (never push & pray), 2 (every stage is a gate),
                      4 (bound the auto-fix loop), 8 (plans are contracts), plus
                      the M7 scope invariant.

  GOVERNANCE_SPECS  — structural invariants of the harness itself (independent of
                      any single run). Graded once by the verify script. Rules:
                      3 (verify locally == remotely), 5 (skills are atoms),
                      6 (deploys are retags), 7 (the pipeline is the product),
                      9 (the harness is governed).

Each `check` returns `(verdict, detail)` where verdict ∈ {"pass", "fail", "na"}.
"na" specs are excluded from the weighted score (weight redistributed), the same
convention `evals/scorers/human.py` uses for un-reviewed cases.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

# Repo root = parent of the evals/ package directory.
_ROOT = Path(__file__).resolve().parent.parent

Verdict = str  # "pass" | "fail" | "na"
CheckFn = Callable[[Any, Dict[str, Any]], Tuple[Verdict, str]]


@dataclass(frozen=True)
class AssertSpec:
    """A single declarative rubric assertion mapped onto a governance Rule."""

    id: str
    rule: str          # human label, e.g. "Rule 2" / "M7 scope"
    title: str
    description: str
    weight: float
    scope: str         # "trial" | "governance"
    check: CheckFn


# --------------------------------------------------------------------------- #
# State helpers (shared by the trial specs)
# --------------------------------------------------------------------------- #
def _gate(state: Any, name: str) -> Any:
    """Return the named GateStatus on the state, or None."""
    for g in getattr(state, "gates", []) or []:
        if getattr(g, "name", None) == name:
            return g
    return None


def _dag_tasks(state: Any) -> List[Any]:
    """Return the task list from the DAG (top-level or nested under plan)."""
    dag = getattr(state, "dag", None) or {}
    tasks = dag.get("tasks") or []
    if not tasks:
        plan = dag.get("plan") or {}
        if isinstance(plan, dict):
            tasks = plan.get("tasks") or plan.get("all_tasks") or []
    return tasks if isinstance(tasks, list) else []


# --------------------------------------------------------------------------- #
# TRIAL specs — graded per run/state
# --------------------------------------------------------------------------- #
def _check_rule1_no_push_and_pray(state: Any, label: Dict[str, Any]) -> Tuple[Verdict, str]:
    """Rule 1: code is verified locally before it could ever be pushed."""
    g = _gate(state, "local_verify")
    if g is None:
        # No code produced yet → nothing to push, nothing to verify.
        if not getattr(state, "code", None):
            return "na", "no code produced — nothing to verify"
        return "fail", "code produced but local_verify never ran (push-and-pray)"
    if getattr(g, "status", None) == "green":
        return "pass", "local_verify green before any push"
    return "fail", f"local_verify={getattr(g, 'status', '?')}: {(g.error_message or '')[:80]}"


def _check_rule2_every_stage_gate(state: Any, label: Dict[str, Any]) -> Tuple[Verdict, str]:
    """Rule 2: every produced artifact passed through its matching gate."""
    pairs = [
        (bool(getattr(state, "contract", None)), "contract_validate", "contract"),
        (bool(getattr(state, "code", None)), "local_verify", "code"),
    ]
    applicable = [p for p in pairs if p[0]]
    if not applicable:
        return "na", "no gateable artifacts produced yet"
    missing = [f"{art}→{gate}" for produced, gate, art in applicable if _gate(state, gate) is None]
    if missing:
        return "fail", "ungated artifacts: " + ", ".join(missing)
    return "pass", f"{len(applicable)} artifact(s) gated"


def _check_rule4_bounded_autofix(state: Any, label: Dict[str, Any]) -> Tuple[Verdict, str]:
    """Rule 4: no gate exceeded the 3-retry budget without escalation."""
    over = [
        f"{getattr(g, 'name', '?')}(retry={getattr(g, 'retry_count', 0)})"
        for g in (getattr(state, "gates", []) or [])
        if getattr(g, "retry_count", 0) > 3
    ]
    if over:
        return "fail", "retry budget exceeded: " + ", ".join(over)
    return "pass", "all gates within the 3-retry budget"


def _check_rule8_plans_are_contracts(state: Any, label: Dict[str, Any]) -> Tuple[Verdict, str]:
    """
    Rule 8: BSA/Architect/Planner outputs form a machine-checkable PEV triple.

    Structural check only — story title+description present, contract has at
    least one path, DAG has tasks. (Acceptance-criteria *quality* is scored
    separately by deterministic.story_has_ac so the two don't double-count.)
    """
    story = getattr(state, "story", None) or {}
    contract = getattr(state, "contract", None) or ""
    problems: List[str] = []
    if not story.get("title"):
        problems.append("story has no title")
    if not story.get("description"):
        problems.append("story has no description")
    if "/" not in contract:
        problems.append("contract has no path")
    if not _dag_tasks(state):
        problems.append("DAG has no tasks")
    if problems:
        return "fail", "; ".join(problems)
    detail = "story + contract + DAG well-formed"
    if getattr(state, "pev_contract", None):
        detail += " (+pev_contract)"
    return "pass", detail


def _check_m7_scope(state: Any, label: Dict[str, Any]) -> Tuple[Verdict, str]:
    """M7 scope invariant: generated code uses exactly the supplied mappings."""
    bm = getattr(state, "business_mappings", None) or []
    if not bm:
        return "na", "no business_mappings supplied (scope guard not engaged)"
    mf = getattr(state, "mapping_fidelity", None)
    if mf is None:
        return "fail", "business_mappings supplied but scope guard never ran"
    if mf.get("exact_match"):
        return "pass", f"{len(mf.get('found', []))} mapping(s) confirmed, none invented"
    extra = mf.get("extra_in_code", [])
    missing = mf.get("missing_in_code", [])
    parts = []
    if missing:
        parts.append("missing: " + ", ".join(missing))
    if extra:
        parts.append("extra: " + ", ".join(extra))
    return "fail", "; ".join(parts) or "scope mismatch"


# --------------------------------------------------------------------------- #
# GOVERNANCE specs — structural invariants of the harness itself
# --------------------------------------------------------------------------- #
def _check_rule3_local_equals_ci(state: Any, label: Dict[str, Any]) -> Tuple[Verdict, str]:
    """Rule 3: the local CI mirror exists so local == remote toolchain."""
    dockerfile = _ROOT / "harness" / "ci-mirror.Dockerfile"
    if dockerfile.exists():
        return "pass", "harness/ci-mirror.Dockerfile present (local mirrors CI)"
    return "fail", "no ci-mirror.Dockerfile — local toolchain may drift from CI"


def _check_rule5_skills_are_atoms(state: Any, label: Dict[str, Any]) -> Tuple[Verdict, str]:
    """Rule 5: every skill is a versioned function under skills/<name>/v*/."""
    skills_dir = _ROOT / "skills"
    if not skills_dir.is_dir():
        return "fail", "no skills/ directory"
    skill_dirs = [p for p in skills_dir.iterdir() if p.is_dir() and not p.name.startswith("_")]
    unversioned = [
        p.name for p in skill_dirs
        if not any(v.is_dir() and v.name.startswith("v") for v in p.iterdir())
    ]
    if unversioned:
        return "fail", "unversioned skills: " + ", ".join(sorted(unversioned)[:5])
    return "pass", f"all {len(skill_dirs)} skill(s) are versioned (v*)"


def _check_rule6_deploys_are_retags(state: Any, label: Dict[str, Any]) -> Tuple[Verdict, str]:
    """Rule 6: production deploys are retags — not exercised in the eval scope."""
    return "na", "no deploy stage in the offline eval pipeline"


def _check_rule7_pipeline_is_product(state: Any, label: Dict[str, Any]) -> Tuple[Verdict, str]:
    """Rule 7: the harness is versioned in git (the pipeline is the product)."""
    if (_ROOT / ".git").exists() and (_ROOT / "orchestrator").is_dir():
        return "pass", "harness is git-versioned; models are swappable config"
    return "na", "harness not in a git checkout (sandboxed copy)"


def _check_rule9_governed_harness(state: Any, label: Dict[str, Any]) -> Tuple[Verdict, str]:
    """Rule 9: human_promote() is the only write path for harness changes."""
    try:
        from evolution import promoter, agent  # noqa: WPS433 (local import by design)
    except Exception as exc:  # noqa: BLE001
        return "fail", f"evolution package not importable: {exc}"
    if not hasattr(promoter, "human_promote"):
        return "fail", "evolution.promoter.human_promote missing"
    # The Evolution Agent must NOT expose an auto-apply path.
    forbidden = [n for n in ("apply", "promote", "human_promote") if hasattr(agent.EvolutionAgent, n)]
    if forbidden:
        return "fail", f"EvolutionAgent exposes write method(s): {forbidden}"
    return "pass", "human_promote() is the sole harness write path; agent cannot auto-apply"


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
TRIAL_SPECS: List[AssertSpec] = [
    AssertSpec(
        id="rule1_no_push_and_pray",
        rule="Rule 1",
        title="Never push and pray",
        description="Code is verified by the local CI mirror before any push.",
        weight=1.0,
        scope="trial",
        check=_check_rule1_no_push_and_pray,
    ),
    AssertSpec(
        id="rule2_every_stage_gate",
        rule="Rule 2",
        title="Every stage is a gate",
        description="Each produced artifact passed through its matching gate.",
        weight=1.5,
        scope="trial",
        check=_check_rule2_every_stage_gate,
    ),
    AssertSpec(
        id="rule4_bounded_autofix",
        rule="Rule 4",
        title="Bound the auto-fix loop",
        description="No gate exceeded the 3-retry budget without escalation.",
        weight=1.5,
        scope="trial",
        check=_check_rule4_bounded_autofix,
    ),
    AssertSpec(
        id="rule8_plans_are_contracts",
        rule="Rule 8",
        title="Plans are contracts",
        description="Story + contract + DAG form a machine-checkable PEV triple.",
        weight=2.0,
        scope="trial",
        check=_check_rule8_plans_are_contracts,
    ),
    AssertSpec(
        id="m7_scope_fidelity",
        rule="M7 scope",
        title="Mapping fidelity (D11 Scope-Guard)",
        description="Generated code uses exactly the supplied business mappings.",
        weight=1.5,
        scope="trial",
        check=_check_m7_scope,
    ),
]

GOVERNANCE_SPECS: List[AssertSpec] = [
    AssertSpec(
        id="rule3_local_equals_ci",
        rule="Rule 3",
        title="Verify locally what CI verifies remotely",
        description="harness/ci-mirror.Dockerfile pins an identical toolchain.",
        weight=1.0,
        scope="governance",
        check=_check_rule3_local_equals_ci,
    ),
    AssertSpec(
        id="rule5_skills_are_atoms",
        rule="Rule 5",
        title="Skills are atoms, not prompts",
        description="Every skill is a versioned function under skills/<name>/v*/.",
        weight=1.0,
        scope="governance",
        check=_check_rule5_skills_are_atoms,
    ),
    AssertSpec(
        id="rule6_deploys_are_retags",
        rule="Rule 6",
        title="Production deploys are retags",
        description="Same image promoted staging→prod; not exercised offline.",
        weight=1.0,
        scope="governance",
        check=_check_rule6_deploys_are_retags,
    ),
    AssertSpec(
        id="rule7_pipeline_is_product",
        rule="Rule 7",
        title="The pipeline is the product",
        description="The harness is versioned in git; models are config.",
        weight=1.0,
        scope="governance",
        check=_check_rule7_pipeline_is_product,
    ),
    AssertSpec(
        id="rule9_governed_harness",
        rule="Rule 9",
        title="The harness is governed",
        description="human_promote() is the only write path for harness changes.",
        weight=1.0,
        scope="governance",
        check=_check_rule9_governed_harness,
    ),
]

ALL_SPECS: List[AssertSpec] = TRIAL_SPECS + GOVERNANCE_SPECS


def all_rule_ids() -> List[str]:
    """Distinct rule labels covered by the registry (for coverage assertions)."""
    seen: List[str] = []
    for s in ALL_SPECS:
        if s.rule not in seen:
            seen.append(s.rule)
    return seen


# --------------------------------------------------------------------------- #
# Evaluators
# --------------------------------------------------------------------------- #
def _safe_check(spec: AssertSpec, state: Any, label: Dict[str, Any]) -> Tuple[Verdict, str]:
    """Run a spec's check, converting any error into a deterministic fail."""
    try:
        verdict, detail = spec.check(state, label)
        if verdict not in ("pass", "fail", "na"):
            return "fail", f"spec returned bad verdict {verdict!r}"
        return verdict, detail
    except Exception as exc:  # noqa: BLE001 — a buggy spec must not crash the eval
        return "fail", f"spec error: {exc}"


def _evaluate(specs: List[AssertSpec], state: Any, label: Dict[str, Any]) -> Dict[str, Any]:
    """Run a list of specs and compute a weighted pass rate over applicable ones."""
    results: Dict[str, Dict[str, Any]] = {}
    weighted_sum = 0.0
    total_weight = 0.0
    n_pass = n_fail = n_na = 0

    for spec in specs:
        verdict, detail = _safe_check(spec, state, label)
        results[spec.id] = {
            "rule": spec.rule,
            "title": spec.title,
            "verdict": verdict,
            "detail": detail,
            "weight": spec.weight,
        }
        if verdict == "na":
            n_na += 1
            continue
        total_weight += spec.weight
        if verdict == "pass":
            n_pass += 1
            weighted_sum += spec.weight
        else:
            n_fail += 1

    # Empty/all-na set scores a clean 1.0 (nothing asserted yet → nothing broken).
    value = round(weighted_sum / total_weight, 4) if total_weight else 1.0
    return {
        "value": value,
        "specs": results,
        "n_pass": n_pass,
        "n_fail": n_fail,
        "n_na": n_na,
        "n_applicable": n_pass + n_fail,
    }


def evaluate_specs(state: Any, label: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Grade the TRIAL specs against a run state. Pure, offline-safe."""
    return _evaluate(TRIAL_SPECS, state, label or {})


def evaluate_governance(state: Any = None, label: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Grade the GOVERNANCE (harness-invariant) specs. State is ignored."""
    return _evaluate(GOVERNANCE_SPECS, state, label or {})


def evaluate_all(state: Any, label: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Grade every spec (trial + governance) against a state."""
    return _evaluate(ALL_SPECS, state, label or {})
