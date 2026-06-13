"""run_regression_evals skill — evaluate a proposal against the CI eval suite."""
from __future__ import annotations

import json
from typing import Any, Dict


async def run_regression_evals(
    proposal_id: str = "",
    proposal_json: str = "",
    fast: str = "true",
) -> Dict[str, Any]:
    """
    Run the CI regression eval suite against a proposed change.

    Args:
        proposal_id:   ID of a proposal in evolution/proposals/pending/ to load.
        proposal_json: JSON string of the proposal (used if proposal_id not given).
        fast:          "true" to use heuristic evaluation (offline-safe);
                       "false" to run the full 15-trial CI suite.

    Returns:
        {improved: bool, delta_pp: float, safe: bool, baseline: dict, current: dict}
    """
    from evolution import evaluator

    proposal: Dict[str, Any] = {}

    if proposal_id:
        from pathlib import Path
        root = Path(__file__).resolve().parents[4]
        pending = root / "evolution" / "proposals" / "pending" / f"{proposal_id}.json"
        if pending.exists():
            proposal = json.loads(pending.read_text(encoding="utf-8"))

    if not proposal and proposal_json:
        try:
            proposal = json.loads(proposal_json)
        except Exception:
            proposal = {}

    if not proposal:
        return {
            "improved": False,
            "delta_pp": 0.0,
            "safe": True,
            "baseline": {},
            "current": {},
            "error": "No proposal provided",
        }

    use_fast = str(fast).lower() not in ("false", "0", "no")
    result = await evaluator.evaluate(proposal, fast=use_fast)
    return result
