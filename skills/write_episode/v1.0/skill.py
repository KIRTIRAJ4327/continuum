"""
write_episode skill — persist an episodic memory record to Neo4j (M3).

Called by the Memory agent at the end of every pipeline run to capture:
  - What decision was made (and by which agent)
  - What action was taken
  - What the outcome was (success / error / partial)
  - Which files/entities were touched (CAUSED relationships)

Live path:   Neo4jDriver.write_episode() → Episode node in graph.
Offline path: Neo4jDriver._in_memory_episodes list (no DB required).
No-driver path: returns a stub dict without touching any store.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


async def write_episode(
    feature_id: str,
    agent: str,
    decision: str,
    action: str,
    outcome: str,
    request_text: str = "",
    story_id: str = "",
    entities: Optional[List[Dict[str, str]]] = None,
    neo4j_driver: Any = None,
) -> Dict[str, Any]:
    """
    Write one Episode node to Neo4j (or in-memory store when offline).

    Args:
        feature_id:   Run identifier / feature key (links to Feature node).
        agent:        Reporting agent role, e.g. "bsa", "architect", "pipeline".
        decision:     Concise description of the decision taken.
        action:       What was actually executed (skill name, commit sha, etc.).
        outcome:      Result — "completed", "retried:N", "failed: <msg>", etc.
        request_text: Original plain-English feature request (enables search).
        story_id:     If set, the Episode is linked to the Story node via ABOUT.
        entities:     Files / tables / endpoints touched: [{"name": ..., "type": ...}].
        neo4j_driver: Injected Neo4jDriver instance (None in offline/test paths).

    Returns:
        {"episode_id": str, "stub": bool, "feature_id": str, "agent": str}
    """
    episode_id = f"ep-{uuid.uuid4().hex[:12]}"

    if neo4j_driver is not None:
        try:
            result = await neo4j_driver.write_episode(
                feature_id=feature_id,
                agent=agent,
                decision=decision,
                action=action,
                outcome=outcome,
                request_text=request_text,
                episode_id=episode_id,
                story_id=story_id,
                entities=entities or [],
            )
            return {
                **result,
                "feature_id": feature_id,
                "agent": agent,
                "decision": decision,
                "action": action,
                "outcome": outcome,
                "request_text": request_text,
                "timestamp": time.time(),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("[write_episode] neo4j_driver call failed: %s", exc)
            # Fall through to stub.

    # Offline / no-driver stub.
    logger.debug("[write_episode] Offline stub — episode_id=%s", episode_id)
    return {
        "episode_id": episode_id,
        "stub": True,
        "feature_id": feature_id,
        "agent": agent,
        "decision": decision,
        "action": action,
        "outcome": outcome,
        "request_text": request_text,
        "timestamp": time.time(),
    }
