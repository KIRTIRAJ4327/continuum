"""
Human-review scorer (~10% weight).

Items where judge.py disagrees with deterministic.py are queued to
evals/results/human_queue.json for manual inspection.

This module does NOT block the eval run — human scores are applied
retrospectively on the next run after the queue is processed.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"
_QUEUE_FILE = _RESULTS_DIR / "human_queue.json"


def _load_queue() -> List[Dict[str, Any]]:
    if _QUEUE_FILE.exists():
        try:
            return json.loads(_QUEUE_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return []
    return []


def _save_queue(queue: List[Dict[str, Any]]) -> None:
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    _QUEUE_FILE.write_text(
        json.dumps(queue, indent=2, default=str),
        encoding="utf-8",
    )


def queue_for_review(
    case_id: str,
    request: str,
    det_score: float,
    judge_score: float,
    judge_reason: str,
    story_title: str = "",
) -> None:
    """
    Add an item to the human review queue.
    Called when judge disagrees with deterministic by >= threshold.
    """
    queue = _load_queue()

    # Deduplicate by case_id — update existing entry instead of appending.
    existing = next((i for i, q in enumerate(queue) if q["case_id"] == case_id), None)
    entry = {
        "case_id": case_id,
        "request": request[:200],
        "story_title": story_title,
        "det_score": round(det_score, 4),
        "judge_score": round(judge_score, 4),
        "disagreement": round(abs(det_score - judge_score), 4),
        "judge_reason": judge_reason,
        "human_score": None,       # filled in manually
        "human_notes": "",
        "queued_at": time.time(),
        "reviewed_at": None,
    }

    if existing is not None:
        queue[existing] = entry
    else:
        queue.append(entry)

    _save_queue(queue)
    logger.info("[human] Queued case %s for review (det=%.2f judge=%.2f)", case_id, det_score, judge_score)


def get_human_score(case_id: str) -> Optional[float]:
    """
    Return the human score for a case if it has been reviewed, else None.
    None causes the eval to skip the human weight for this case.
    """
    queue = _load_queue()
    for item in queue:
        if item["case_id"] == case_id and item.get("human_score") is not None:
            return float(item["human_score"])
    return None


def score(
    case_id: str,
    request: str,
    det_score: float,
    judge_result: Dict[str, Any],
    story_title: str = "",
) -> Dict[str, Any]:
    """
    Compute the human-weight component for a case.

    If a human score exists → use it.
    If judge needs review → queue and return None (excluded from weighted avg).
    Otherwise → pass-through the judge value (no human needed).

    Returns:
        {
            "value": float | None,  # None = excluded this run
            "reason": str,
            "queued": bool,
        }
    """
    judge_score = judge_result.get("value", 0.0)
    needs_review = judge_result.get("needs_human_review", False)

    # Check if already reviewed.
    human = get_human_score(case_id)
    if human is not None:
        return {
            "value": human,
            "reason": f"human_reviewed: {human:.2f}",
            "queued": False,
        }

    if needs_review:
        queue_for_review(
            case_id=case_id,
            request=request,
            det_score=det_score,
            judge_score=judge_score,
            judge_reason=judge_result.get("reason", ""),
            story_title=story_title,
        )
        return {
            "value": None,
            "reason": "queued_for_human_review",
            "queued": True,
        }

    # No disagreement — no human review needed, exclude from weight.
    return {
        "value": None,
        "reason": "no_review_needed",
        "queued": False,
    }
