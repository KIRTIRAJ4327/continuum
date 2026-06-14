"""
Rubric scorer (~30% weight in the eval mix) — ASSERT-based as of M10.

Historically this was a bespoke Azure 1–5 LLM "judge" of BSA story quality. M10
replaces that opaque rating with declarative ASSERT specs (see
`evals/assert_specs.py`): one or more machine-checkable rubrics per governance
Rule (1–9) plus the M7 scope invariant. The primary `value` is now the weighted
pass rate over the *applicable trial specs* for the state — fully deterministic
and offline.

The public contract is unchanged so `pass_k_runner.py`, `ci_gate.py`, and
`human.py` keep working untouched:

    score(state, label) -> {
        "value": float,                # [0,1] — ASSERT weighted pass rate
        "reason": str,
        "method": "assert" | "assert+llm",
        "specs": {id: {...}},          # NEW: per-spec verdicts (auditable)
        "heuristic_value": float,      # kept for back-compat (== value offline)
        "needs_human_review": bool,    # True when any spec fails / LLM disagrees
        "llm_rating": int | None,      # optional 1–5 enrichment when live
    }

Live enrichment: when Azure credentials are present we still ask the model for a
1–5 story-quality rating, but only as a *secondary signal* surfaced under
`llm_rating` and used to flag calibration disagreements — it no longer drives the
score. With no credentials the call is skipped and the result is identical every
run.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Tuple

from evals.assert_specs import evaluate_specs
from orchestrator.state import ContinuumState

logger = logging.getLogger(__name__)

# A 1–5 LLM rating this far (normalised) from the ASSERT score → flag for review.
_DISAGREE_THRESHOLD = 0.25


def _assert_reason(result: Dict[str, Any]) -> str:
    """One-line human summary of the ASSERT verdicts."""
    n_pass = result["n_pass"]
    n_app = result["n_applicable"]
    base = f"assert: {n_pass}/{n_app} trial specs pass"
    fails = [
        f"{v['rule']} ({v['detail']})"
        for v in result["specs"].values()
        if v["verdict"] == "fail"
    ]
    if fails:
        return base + " — FAIL: " + "; ".join(fails[:3])
    return base


async def _llm_rating(state: ContinuumState) -> Optional[int]:
    """
    Optional live enrichment: ask Azure OpenAI for a 1–5 story-quality rating.
    Returns None when credentials are absent or the call fails. Never raises.
    """
    api_key = os.getenv("AZURE_OPENAI_API_KEY", "")
    endpoint = os.getenv("AZURE_AI_ENDPOINT") or os.getenv("AZURE_OPENAI_ENDPOINT") or ""
    deployment = os.getenv("AZURE_DEPLOYMENT_CHEAP") or os.getenv("AZURE_DEPLOYMENT_STRONG") or ""
    if not (api_key and endpoint and deployment):
        return None

    story = state.story or {}
    story_text = (
        f"Title: {story.get('title', '')}\n"
        f"Description: {story.get('description', '')}\n"
        "Acceptance Criteria:\n"
        + "\n".join(f"- {ac}" for ac in (story.get("acceptance_criteria") or []))
    )
    prompt = (
        "Rate this AI-generated user story 1-5 on completeness and clarity. "
        "Respond with ONLY a single integer (1-5).\n\n"
        f"Story:\n{story_text[:2000]}"
    )
    try:
        import httpx
        url = f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version=2024-08-01-preview"
        payload = {"messages": [{"role": "user", "content": prompt}], "max_tokens": 5, "temperature": 0}
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                url, json=payload,
                headers={"api-key": api_key, "Content-Type": "application/json"},
            )
        if resp.status_code == 200:
            raw = resp.json()["choices"][0]["message"]["content"].strip()
            rating = int(raw[0])
            if 1 <= rating <= 5:
                return rating
    except Exception as exc:  # noqa: BLE001
        logger.warning("[judge] LLM enrichment failed: %s", exc)
    return None


async def score(
    state: ContinuumState,
    label: Dict[str, Any],
) -> Dict[str, Any]:
    """Score a run against the ASSERT trial specs (with optional LLM enrichment)."""
    result = evaluate_specs(state, label)
    value: float = result["value"]
    needs_review = result["n_fail"] > 0

    rating = await _llm_rating(state)
    if rating is not None:
        llm_norm = (rating - 1) / 4.0  # map 1–5 → 0–1
        # Flag calibration disagreement between the rubric and the LLM's gestalt.
        if abs(llm_norm - value) >= _DISAGREE_THRESHOLD:
            needs_review = True
        return {
            "value": value,
            "reason": _assert_reason(result) + f"; llm_rating={rating}/5",
            "method": "assert+llm",
            "specs": result["specs"],
            "heuristic_value": value,
            "needs_human_review": needs_review,
            "llm_rating": rating,
        }

    return {
        "value": value,
        "reason": _assert_reason(result),
        "method": "assert",
        "specs": result["specs"],
        "heuristic_value": value,
        "needs_human_review": needs_review,
        "llm_rating": None,
    }


# Backwards-compatible alias: some callers/tests imported the private heuristic.
def _heuristic_score(state: ContinuumState) -> Tuple[float, str]:
    """Deprecated shim — returns the ASSERT trial score as a (value, reason) pair."""
    result = evaluate_specs(state, {})
    return result["value"], _assert_reason(result)
