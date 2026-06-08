"""
graphrag_query skill — retrieve relevant past episodes from Neo4j (M3 GraphRAG).

Live path:   calls Neo4jDriver.get_similar_episodes() — fulltext + Cypher.
Offline path: Neo4jDriver._offline_search() — in-memory word-overlap scoring.
No-driver path: returns [] so BSA/Architect degrade gracefully without Neo4j.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


async def graphrag_query(
    query: str,
    neo4j_driver: Any = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """
    Retrieve up to `limit` past episodes semantically related to `query`.

    Args:
        query:        The current feature request or agent question.
        neo4j_driver: Injected Neo4jDriver instance (or None in offline tests).
        limit:        Maximum episodes to return (default 5).

    Returns:
        List of episode dicts, each with:
            id, feature_id, agent, decision, action, outcome,
            request_text, timestamp, score
        Empty list when driver is absent or no matches found.
    """
    if neo4j_driver is None:
        logger.debug("[graphrag_query] No Neo4j driver — returning empty (offline)")
        return []

    if not query or not query.strip():
        logger.debug("[graphrag_query] Empty query — returning empty")
        return []

    try:
        episodes: List[Dict[str, Any]] = await neo4j_driver.get_similar_episodes(
            query_text=query.strip(),
            limit=int(limit),
        )
        logger.info(
            "[graphrag_query] Found %d episodes for query: %.60s",
            len(episodes),
            query,
        )
        return episodes
    except Exception as exc:  # noqa: BLE001
        logger.warning("[graphrag_query] Query failed: %s", exc)
        return []
