"""
evolution/agent.py -- observe, diagnose, and propose harness improvements.

The Evolution Agent is a read-only observer that:
  1. observe()  — scans the event bus history to identify recurring failure patterns.
  2. diagnose() — classifies each pattern (prompt issue / skill gap / gate too strict
                  / routing error).
  3. propose()  — produces a concrete harness change as a structured diff proposal.

CRITICAL: This agent NEVER auto-applies changes (Rule 9: governed mutation).
Every proposal is written to evolution/proposals/pending/ and requires a
human_promote() call to take effect.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_PENDING_DIR = _ROOT / "evolution" / "proposals" / "pending"

# Canned failure patterns used when the event bus has no history (offline/cold start).
_OFFLINE_PATTERNS: List[Dict[str, Any]] = [
    {
        "agent": "frontend",
        "failure_type": "gate_red",
        "gate": "local_verify",
        "count": 3,
        "fraction": 0.60,
        "sample_error": "ruff: line too long (105 > 100 chars)",
    },
    {
        "agent": "bsa",
        "failure_type": "low_ac_count",
        "gate": None,
        "count": 2,
        "fraction": 0.40,
        "sample_error": "story has fewer than 2 acceptance criteria",
    },
]

# Proposal templates keyed by diagnosis type.
_PROPOSAL_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "gate_too_strict": {
        "type": "gate_threshold",
        "file": "orchestrator/gates.py",
        "before": "# ruff line-length gate",
        "after":  "# ruff line-length gate (adjusted)",
        "rationale": (
            "local_verify fires for ruff line-length on auto-generated frontend files. "
            "Relaxing the check for generated TypeScript reduces false-positive gate failures."
        ),
    },
    "prompt_issue": {
        "type": "prompt_edit",
        "file": "agents/bsa.yaml",
        "before": "acceptance_criteria: []",
        "after":  "acceptance_criteria: []  # minimum 3 criteria required",
        "rationale": (
            "BSA produces stories with too few acceptance criteria. "
            "Adding an explicit minimum in the instructions increases story quality."
        ),
    },
    "skill_gap": {
        "type": "routing_rule",
        "file": "orchestrator/graph.py",
        "before": "state.next_agent = AgentRole.DATABASE",
        "after":  "state.next_agent = AgentRole.DATABASE  # schema-first enforced",
        "rationale": (
            "Planner occasionally routes to BACKEND before DATABASE is complete. "
            "Explicit comment in routing logic acts as a guardrail."
        ),
    },
    "routing_error": {
        "type": "routing_rule",
        "file": "orchestrator/agent_runner.py",
        "before": "return {}",
        "after":  "return {}  # evolution: no-op guard",
        "rationale": (
            "Unrecognised role falls through to empty dict. "
            "Adding an explicit guard makes routing errors visible in logs."
        ),
    },
}


class EvolutionAgent:
    """Observe pipeline telemetry, diagnose issues, and propose targeted fixes."""

    def observe(self, event_log: Optional[Dict[str, List[Dict]]] = None) -> List[Dict[str, Any]]:
        """
        Scan event history for recurring failure patterns.

        Args:
            event_log: run_id → events dict (from event_bus._log).
                       If None, the live singleton is used; if empty, canned patterns
                       are returned so the offline path always produces something.

        Returns:
            List of pattern dicts: {agent, failure_type, gate, count, fraction, sample_error}.
        """
        if event_log is None:
            try:
                from orchestrator.events import event_bus
                event_log = dict(event_bus._log)
            except Exception:
                event_log = {}

        if not event_log:
            logger.info("[EVO] No event history found — returning offline patterns")
            return list(_OFFLINE_PATTERNS)

        # Tally gate_red events per (agent, gate).
        failure_counts: Dict[tuple, int] = defaultdict(int)
        total_trials: Dict[str, int] = defaultdict(int)
        sample_errors: Dict[tuple, str] = {}

        for run_id, events in event_log.items():
            for ev in events:
                agent = ev.get("agent", "unknown")
                total_trials[agent] += 1
                if ev.get("event_type") == "gate_red":
                    gate = ev.get("gate", "unknown")
                    key = (agent, gate)
                    failure_counts[key] += 1
                    if key not in sample_errors:
                        sample_errors[key] = (ev.get("data") or {}).get("output", "")[:200]

        if not failure_counts:
            return list(_OFFLINE_PATTERNS)

        patterns = []
        for (agent, gate), count in sorted(failure_counts.items(), key=lambda x: -x[1]):
            total = max(total_trials.get(agent, 1), 1)
            patterns.append({
                "agent": agent,
                "failure_type": "gate_red",
                "gate": gate,
                "count": count,
                "fraction": round(count / total, 2),
                "sample_error": sample_errors.get((agent, gate), ""),
            })

        logger.info("[EVO] Observed %d failure pattern(s) across %d runs", len(patterns), len(event_log))
        return patterns

    def diagnose(self, patterns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Classify each pattern into a diagnosis type.

        Returns:
            List of diagnosis dicts: {pattern, diagnosis_type, confidence, recommendation}.
        """
        diagnoses = []
        for p in patterns:
            gate = p.get("gate", "")
            fraction = p.get("fraction", 0.0)
            agent = p.get("agent", "")
            failure_type = p.get("failure_type", "")

            if failure_type == "gate_red" and fraction >= 0.5:
                dtype = "gate_too_strict"
                recommendation = f"Relax or tune {gate} gate threshold for {agent} agent"
                confidence = 0.8 if fraction >= 0.7 else 0.6
            elif failure_type == "low_ac_count":
                dtype = "prompt_issue"
                recommendation = "Add explicit minimum AC count to BSA instructions"
                confidence = 0.9
            elif gate == "local_verify" and agent in ("frontend", "developer"):
                dtype = "gate_too_strict"
                recommendation = "Check ruff/mypy config for generated-file exclusions"
                confidence = 0.7
            elif agent in ("planner", "architect"):
                dtype = "routing_error"
                recommendation = "Review routing logic for this agent"
                confidence = 0.5
            else:
                dtype = "skill_gap"
                recommendation = f"Review {agent} skill set — may need new skill"
                confidence = 0.4

            diagnoses.append({
                "pattern": p,
                "diagnosis_type": dtype,
                "confidence": confidence,
                "recommendation": recommendation,
            })

        return diagnoses

    def propose(self, diagnoses: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        Generate a single concrete harness change proposal.

        Picks the highest-confidence diagnosis and returns a proposal dict:
          {id, type, file, before, after, rationale, diagnosis_type,
           estimated_improvement_pp, created_at}

        The proposal is also written to evolution/proposals/pending/ (but NOT applied).
        """
        if not diagnoses:
            diagnoses = self.diagnose(self.observe())

        if not diagnoses:
            logger.warning("[EVO] No diagnoses to propose from")
            return {}

        # Pick highest-confidence diagnosis.
        best = max(diagnoses, key=lambda d: d.get("confidence", 0))
        dtype = best["diagnosis_type"]
        template = _PROPOSAL_TEMPLATES.get(dtype, _PROPOSAL_TEMPLATES["skill_gap"])

        proposal_id = f"evo-{uuid.uuid4().hex[:8]}"
        proposal: Dict[str, Any] = {
            "id": proposal_id,
            "type": template["type"],
            "file": template["file"],
            "before": template["before"],
            "after": template["after"],
            "rationale": template["rationale"],
            "diagnosis_type": dtype,
            "confidence": best.get("confidence", 0.5),
            "pattern": best.get("pattern", {}),
            "recommendation": best.get("recommendation", ""),
            "estimated_improvement_pp": round(best.get("confidence", 0.5) * 10, 1),
            "created_at": int(time.time()),
            "status": "pending",
        }

        _write_pending(proposal)
        logger.info("[EVO] Proposal %s written to pending/ (type=%s, file=%s)", proposal_id, dtype, template["file"])
        return proposal


def _write_pending(proposal: Dict[str, Any]) -> Path:
    """Write a proposal JSON to evolution/proposals/pending/."""
    _PENDING_DIR.mkdir(parents=True, exist_ok=True)
    path = _PENDING_DIR / f"{proposal['id']}.json"
    path.write_text(json.dumps(proposal, indent=2), encoding="utf-8")
    return path


def list_pending() -> List[Dict[str, Any]]:
    """Return all pending proposals sorted by creation time (newest first)."""
    if not _PENDING_DIR.exists():
        return []
    proposals = []
    for p in _PENDING_DIR.glob("*.json"):
        try:
            proposals.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            pass
    return sorted(proposals, key=lambda x: x.get("created_at", 0), reverse=True)
