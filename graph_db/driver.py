"""Neo4j async driver — connection + DAG read/write for the orchestrator."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

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


class Neo4jDriver:
    """Thin async wrapper around `neo4j.AsyncGraphDatabase` for Continuum's DAG."""

    def __init__(self, uri: str, user: str, password: str):
        self.uri = uri
        self.user = user
        self.password = password
        self.driver: Optional[Any] = None  # neo4j.AsyncDriver

    async def connect(self) -> None:
        """Open an async driver. Idempotent — second call is a no-op."""
        if self.driver is not None:
            return
        try:
            from neo4j import AsyncGraphDatabase  # lazy import
        except ImportError as exc:  # pragma: no cover - require neo4j at runtime only
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

    async def write_dag(self, feature_id: str, dag: Dict[str, Any]) -> bool:
        """
        Persist a task DAG to Neo4j.

        `dag` shape (from the Architect / `write_dag` skill):
            {"tasks": [{"id": str, "title": str, "depends_on": [str]}, ...]}

        Idempotent: re-running with the same feature_id MERGEs nodes and edges.
        Returns True on success.
        """
        if self.driver is None:
            await self.connect()
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
            await self.connect()
        async with self.driver.session() as session:  # type: ignore[union-attr]
            result = await session.run(_UNBLOCKED_TASKS, feature_id=feature_id)
            records = await result.data()
        return list(records)

    async def update_task_status(self, task_id: str, status: str) -> bool:
        """Update a task's status. Returns False if no task matched."""
        if self.driver is None:
            await self.connect()
        async with self.driver.session() as session:  # type: ignore[union-attr]
            result = await session.run(_UPDATE_STATUS, task_id=task_id, status=status)
            records = await result.data()
        return bool(records)
