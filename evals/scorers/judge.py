"""
LLM-as-judge scorer (~30% weight in the eval mix).

Live path:   Azure OpenAI rates BSA story quality 1-5.
Offline path: heuristic based on word count and acceptance-criteria count.

Calibration tracking:
  judge_score and deterministic_story_score are stored in results so the
  pipeline can flag disagreements for human review (see human.py).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Tuple

from orchestrator.state import ContinuumState

logger = logging.getLogger(__name__)

# Threshold below which LLM and heuristic are considered "disagreeing".
_DISAGREE_THRESHOLD = 0.25


def _heuristic_score(state: ContinuumState) -> Tuple[float, str]:
    """
    Offline fallback: score BSA story quality via cheap structural heuristics.

    Scoring rubric (max 5 points, normalised to [0, 1]):
      +1  title is present and non-trivial (>= 5 words)
      +1  description is present and non-trivial (>= 20 words)
      +1  at least 2 acceptance criteria
      +1  at least 4 acceptance criteria
      +1  spec block present (overview + key_concepts)
    """
    story = state.story or {}
    points = 0.0
    reasons = []

    title = (story.get("title") or "").strip()
    if len(title.split()) >= 5:
        points += 1
        reasons.append("title>=5words")
    elif title:
        points += 0.5
        reasons.append("title_short")

    description = (story.get("description") or "").strip()
    if len(description.split()) >= 20:
        points += 1
        reasons.append("desc>=20words")
    elif description:
        points += 0.5
        reasons.append("desc_short")

    ac = story.get("acceptance_criteria") or []
    if not isinstance(ac, list):
        ac = []
    if len(ac) >= 4:
        points += 2
        reasons.append(f"ac={len(ac)}")
    elif len(ac) >= 2:
        points += 1
        reasons.append(f"ac={len(ac)}")

    spec = story.get("spec") or {}
    if spec.get("overview") and spec.get("key_concepts"):
        points += 1
        reasons.append("spec_complete")
    elif spec.get("overview"):
        points += 0.5
        reasons.append("spec_partial")

    normalized = round(points / 5.0, 4)
    return normalized, "heuristic: " + ", ".join(reasons) if reasons else "heuristic: empty story"


async def _llm_score(state: ContinuumState) -> Optional[Tuple[float, str]]:
    """
    Call Azure OpenAI to rate the BSA story 1-5.
    Returns None if credentials are absent or call fails.
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
        f"Acceptance Criteria:\n"
        + "\n".join(f"- {ac}" for ac in (story.get("acceptance_criteria") or []))
        + f"\n\nSpec overview: {(story.get('spec') or {}).get('overview', '')}"
    )

    prompt = (
        "You are evaluating a user story produced by an AI Business Analyst.\n\n"
        "Rate the story from 1 to 5 on completeness and clarity:\n"
        "  1 = missing title or acceptance criteria\n"
        "  2 = minimal content, unclear scope\n"
        "  3 = adequate — covers main use case, has some AC\n"
        "  4 = good — clear title, 3+ AC, scoped spec\n"
        "  5 = excellent — detailed AC, measurable criteria, edge cases covered\n\n"
        "Respond with ONLY a single integer (1-5). No explanation.\n\n"
        f"Story:\n{story_text[:2000]}"
    )

    try:
        import httpx
        url = f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version=2024-08-01-preview"
        payload = {
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 5,
            "temperature": 0,
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={"api-key": api_key, "Content-Type": "application/json"},
            )
        if resp.status_code == 200:
            raw = resp.json()["choices"][0]["message"]["content"].strip()
            rating = int(raw[0])  # first char is the digit
            if 1 <= rating <= 5:
                normalized = (rating - 1) / 4.0  # map 1-5 → 0-1
                return round(normalized, 4), f"llm_judge: rating={rating}/5"
    except Exception as exc:  # noqa: BLE001
        logger.warning("[judge] LLM call failed: %s", exc)
    return None


async def score(
    state: ContinuumState,
    label: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Score story quality using LLM judge with heuristic fallback.

    Returns:
        {
            "value": float,       # [0, 1]
            "reason": str,
            "method": "llm" | "heuristic",
            "needs_human_review": bool,
        }
    """
    heuristic_val, heuristic_reason = _heuristic_score(state)

    llm_result = await _llm_score(state)
    if llm_result is not None:
        llm_val, llm_reason = llm_result
        # Flag for human review when LLM and heuristic disagree significantly.
        needs_review = abs(llm_val - heuristic_val) >= _DISAGREE_THRESHOLD
        return {
            "value": llm_val,
            "reason": llm_reason,
            "method": "llm",
            "heuristic_value": heuristic_val,
            "needs_human_review": needs_review,
        }

    return {
        "value": heuristic_val,
        "reason": heuristic_reason,
        "method": "heuristic",
        "heuristic_value": heuristic_val,
        "needs_human_review": False,
    }
