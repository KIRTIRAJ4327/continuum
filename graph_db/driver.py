"""Neo4j async driver — connection + DAG read/write + episodic memory (M3)."""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── DAG queries ──────────────────────────────────────────────────────────────
# Idempotent DAG write — MERGE so retries/recoveries don't double-create nodes
# or edges. Splits the upsert into three steps so an empty `depends_on` is fine.
_WRITE_FEATURE = """
MERGE (f:Feature {id: $feature_id})
ON CREATE SET f.created_at = datetime()
RETURN f.id AS id
"""

_WRITE_TASK = """
MERGE (t:Task {id: $task_id})
ON CREATE SET t.status = 'pending', t.created_at = datetime()
SET t.title = $title
WITH t
MATCH (f:Feature {id: $feature_id})
MERGE (f)-[:HAS_TASK]->(t)
"""

_WRITE_EDGE = """
MATCH (t:Task {id: $task_id}), (dep:Task {id: $dep_id})
MERGE (t)-[:DEPENDS_ON]->(dep)
"""

# Scoped to one feature so multiple runs in the same DB don't tangle.
_UNBLOCKED_TASKS = """
MATCH (f:Feature {id: $feature_id})-[:HAS_TASK]->(t:Task {status: 'pending'})
WHERE NONE(d IN [(t)-[:DEPENDS_ON]->(dep) | dep] WHERE d.status <> 'done')
RETURN t.id AS id, t.title AS title, t.status AS status
"""

_UPDATE_STATUS = """
MATCH (t:Task {id: $task_id})
SET t.status = $status
RETURN t.id AS id
"""

# ── M3: Episodic memory queries ───────────────────────────────────────────────
_WRITE_EPISODE = """
MERGE (e:Episode {id: $episode_id})
ON CREATE SET
    e.feature_id  = $feature_id,
    e.agent       = $agent,
    e.decision    = $decision,
    e.action      = $action,
    e.outcome     = $outcome,
    e.request_text = $request_text,
    e.timestamp   = $timestamp
WITH e
MERGE (f:Feature {id: $feature_id})
ON CREATE SET f.created_at = datetime()
MERGE (f)-[:HAS_EPISODE]->(e)
"""

_LINK_EPISODE_STORY = """
MATCH (e:Episode {id: $episode_id})
MATCH (s:Story   {id: $story_id})
MERGE (e)-[:ABOUT]->(s)
"""

_WRITE_ENTITY = """
MERGE (n:Entity {id: $entity_id})
ON CREATE SET n.name = $name, n.type = $etype
WITH n
MATCH (e:Episode {id: $episode_id})
MERGE (e)-[:CAUSED]->(n)
"""

# Full-text search (uses index created by schema.cypher).
_EPISODE_FULLTEXT = """
CALL db.index.fulltext.queryNodes('episode_search', $query)
YIELD node AS e, score
RETURN
    e.id           AS id,
    e.feature_id   AS feature_id,
    e.agent        AS agent,
    e.decision     AS decision,
    e.action       AS action,
    e.outcome      AS outcome,
    e.request_text AS request_text,
    e.timestamp    AS timestamp,
    score
ORDER BY score DESC
LIMIT $limit
"""

# Fallback: recent-episodes scan when fulltext index doesn't exist yet.
_EPISODE_RECENT = """
MATCH (e:Episode)
RETURN
    e.id           AS id,
    e.feature_id   AS feature_id,
    e.agent        AS agent,
    e.decision     AS decision,
    e.action       AS action,
    e.outcome      AS outcome,
    e.request_text AS request_text,
    e.timestamp    AS timestamp,
    1.0            AS score
ORDER BY e.timestamp DESC
LIMIT $limit
"""


class Neo4jDriver:
    """
    Thin async wrapper around `neo4j.AsyncGraphDatabase` for Continuum.

    Offline / in-memory fallback (M3):
        When `self.driver` is None (no connection), all write_episode() calls
        accumulate in `self._in_memory_episodes` and get_similar_episodes()
        performs a simple word-overlap search over that list.  This lets the
        verify_m3_learning script demonstrate the learning-lift loop without a
        running Neo4j instance.
    """

    def __init__(self, uri: str, user: str, password: str):
        self.uri = uri
        self.user = user
        self.password = password
        self.driver: Optional[Any] = None  # neo4j.AsyncDriver
        # In-memory store used when Neo4j is not reachable (offline path).
        self._in_memory_episodes: List[Dict[str, Any]] = []

    # ── Connection ────────────────────────────────────────────────────────────
    async def connect(self) -> None:
        """Open an async driver. Idempotent — second call is a no-op."""
        if self.driver is not None:
            return
        try:
            from neo4j import AsyncGraphDatabase  # lazy import
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "neo4j package not installed; run `pip install neo4j>=5.15`"
            ) from exc
        self.driver = AsyncGraphDatabase.driver(self.uri, auth=(self.user, self.password))
        logger.info("Neo4j driver connected to %s", self.uri)

    async def close(self) -> None:
        """Close the driver if it was opened."""
        if self.driver is None:
            return
        await self.driver.close()
        self.driver = None
        logger.info("Neo4j driver closed")

    # ── DAG operations ────────────────────────────────────────────────────────
    async def write_dag(self, feature_id: str, dag: Dict[str, Any]) -> bool:
        """
        Persist a task DAG to Neo4j.

        `dag` shape (from the Architect / `write_dag` skill):
            {"tasks": [{"id": str, "title": str, "depends_on": [str]}, ...]}

        Idempotent: re-running with the same feature_id MERGEs nodes and edges.
        Returns True on success.
        """
        if self.driver is None:
            logger.debug("write_dag: no driver — skipping (offline)")
            return False
        tasks = (dag or {}).get("tasks") or []
        if not tasks:
            logger.warning("write_dag: no tasks in dag")
            return False

        async with self.driver.session() as session:  # type: ignore[union-attr]
            await session.run(_WRITE_FEATURE, feature_id=feature_id)
            for task in tasks:
                tid = task.get("id")
                if not tid:
                    continue
                await session.run(
                    _WRITE_TASK,
                    feature_id=feature_id,
                    task_id=tid,
                    title=task.get("title", tid),
                )
            for task in tasks:
                tid = task.get("id")
                for dep_id in task.get("depends_on") or []:
                    await session.run(_WRITE_EDGE, task_id=tid, dep_id=dep_id)

        logger.info("write_dag: persisted %d tasks for feature %s", len(tasks), feature_id)
        return True

    async def get_unblocked_tasks(self, feature_id: str) -> List[Dict[str, Any]]:
        """Return pending tasks whose dependencies are all done."""
        if self.driver is None:
            return []
        async with self.driver.session() as session:  # type: ignore[union-attr]
            result = await session.run(_UNBLOCKED_TASKS, feature_id=feature_id)
            records = await result.data()
        return list(records)

    async def update_task_status(self, task_id: str, status: str) -> bool:
        """Update a task's status. Returns False if no task matched."""
        if self.driver is None:
            return False
        async with self.driver.session() as session:  # type: ignore[union-attr]
            result = await session.run(_UPDATE_STATUS, task_id=task_id, status=status)
            records = await result.data()
        return bool(records)

    # ── M3: Episodic memory ───────────────────────────────────────────────────
    async def write_episode(
        self,
        feature_id: str,
        agent: str,
        decision: str,
        action: str,
        outcome: str,
        request_text: str = "",
        episode_id: str = "",
        story_id: str = "",
        entities: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """
        Persist one Episode node and link it to its Feature (and optionally Story).

        Args:
            feature_id:   The run_id / story feature key.
            agent:        Which agent made this decision (e.g. "bsa", "architect").
            decision:     What was decided (free text).
            action:       What action was taken.
            outcome:      What happened — "completed", "retried", error text, etc.
            request_text: Original feature request (used for semantic search).
            episode_id:   Optional pre-computed ID; auto-generated if absent.
            story_id:     Optional Story node to link via ABOUT.
            entities:     Optional list of {"name": str, "type": str} dicts (files,
                          tables, endpoints) to link via CAUSED.

        Returns:
            {"episode_id": str, "stub": bool}
        """
        ep_id = episode_id or f"ep-{uuid.uuid4().hex[:12]}"
        ts = time.time()
        record: Dict[str, Any] = {
            "id":           ep_id,
            "feature_id":   feature_id,
            "agent":        agent,
            "decision":     decision,
            "action":       action,
            "outcome":      outcome,
            "request_text": request_text,
            "timestamp":    ts,
        }

        if self.driver is None:
            # Offline / in-memory path — no Neo4j.
            self._in_memory_episodes.append(record)
            logger.debug("[memory] Stored episode %s in-memory (%d total)", ep_id, len(self._in_memory_episodes))
            return {"episode_id": ep_id, "stub": True}

        # Live Neo4j path.
        try:
            async with self.driver.session() as session:  # type: ignore[union-attr]
                await session.run(
                    _WRITE_EPISODE,
                    episode_id=ep_id,
                    feature_id=feature_id,
                    agent=agent,
                    decision=decision,
                    action=action,
                    outcome=outcome,
                    request_text=request_text,
                    timestamp=ts,
                )
                if story_id:
                    await session.run(_LINK_EPISODE_STORY, episode_id=ep_id, story_id=story_id)
                for ent in (entities or []):
                    ent_id = f"ent-{uuid.uuid4().hex[:8]}"
                    await session.run(
                        _WRITE_ENTITY,
                        episode_id=ep_id,
                        entity_id=ent_id,
                        name=ent.get("name", "unknown"),
                        etype=ent.get("type", "file"),
                    )
            logger.info("[memory] Episode %s written to Neo4j (feature=%s agent=%s)", ep_id, feature_id, agent)
            return {"episode_id": ep_id, "stub": False}
        except Exception as exc:  # noqa: BLE001
            logger.warning("[memory] write_episode failed, falling back to in-memory: %s", exc)
            self._in_memory_episodes.append(record)
            return {"episode_id": ep_id, "stub": True}

    async def get_similar_episodes(
        self, query_text: str, limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Return the top-k episodes most similar to `query_text`.

        Live path:  fulltext search via `episode_search` index (schema.cypher).
        Offline path: word-overlap scoring over `_in_memory_episodes`.

        Each returned dict has keys:
            id, feature_id, agent, decision, action, outcome, request_text, timestamp, score
        """
        if self.driver is None:
            return self._offline_search(query_text, limit)

        try:
            # Escape special Lucene characters in the query.
            safe_q = _escape_lucene(query_text)
            async with self.driver.session() as session:  # type: ignore[union-attr]
                result = await session.run(_EPISODE_FULLTEXT, query=safe_q, limit=limit)
                rows = await result.data()
            if rows:
                return [dict(r) for r in rows]
            # Fulltext index may not exist yet — fall back to recency scan.
            async with self.driver.session() as session:  # type: ignore[union-attr]
                result = await session.run(_EPISODE_RECENT, limit=limit)
                rows = await result.data()
            return [dict(r) for r in rows]
        except Exception as exc:  # noqa: BLE001
            logger.warning("[memory] get_similar_episodes failed, using in-memory: %s", exc)
            return self._offline_search(query_text, limit)

    def _offline_search(self, query_text: str, limit: int) -> List[Dict[str, Any]]:
        """Word-overlap scoring over the in-memory episode list."""
        if not self._in_memory_episodes:
            return []
        query_words = set(query_text.lower().split())
        scored: List[Dict[str, Any]] = []
        for ep in self._in_memory_episodes:
            blob = " ".join([
                ep.get("decision", ""),
                ep.get("action", ""),
                ep.get("outcome", ""),
                ep.get("request_text", ""),
            ]).lower()
            ep_words = set(blob.split())
            overlap = len(query_words & ep_words)
            if overlap > 0:
                score = overlap / max(len(query_words), 1)
                scored.append({**ep, "score": round(score, 4)})
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]


# ── Helpers ──────────────────────────────────────────────────────────────────
def _escape_lucene(text: str) -> str:
    """Escape special Lucene characters so user text doesn't break the query."""
    special = r'\+-&|!(){}[]^"~*?:/'
    escaped = "".join(f"\\{c}" if c in special else c for c in text)
    # Truncate to avoid query-too-long errors.
    return escaped[:500]
