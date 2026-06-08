"""propose_patch skill — generate a concrete harness change from a failure pattern."""
from __future__ import annotations

from typing import Any, Dict


async def propose_patch(
    failure_pattern: str = "",
    diagnosis_type: str = "gate_too_strict",
    target_file: str = "",
) -> Dict[str, Any]:
    """
    Generate a concrete proposal diff based on a diagnosed failure pattern.

    Returns a proposal dict that can be passed to evolution/evaluator.py.
    """
    from evolution.agent import EvolutionAgent, _PROPOSAL_TEMPLATES

    agent = EvolutionAgent()

    # Parse failure_pattern if JSON string was passed.
    pattern: Dict[str, Any] = {}
    if failure_pattern:
        try:
            import json
            pattern = json.loads(failure_pattern)
        except Exception:
            pattern = {"raw": failure_pattern}

    diagnoses = [
        {
            "pattern": pattern,
            "diagnosis_type": diagnosis_type,
            "confidence": 0.75,
            "recommendation": f"Address {diagnosis_type} in {target_file or 'harness'}",
        }
    ]

    proposal = agent.propose(diagnoses)
    return proposal
