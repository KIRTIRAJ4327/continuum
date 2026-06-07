"""
query_dag skill — reads the DAG from Neo4j and identifies unblocked tasks.

Live path:  Real Neo4j query via the driver's get_unblocked_tasks().
Offline path: Returns a deterministic structure keyed to the feature_id.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


async def query_dag(feature_id: str, neo4j_driver: Any = None) -> Dict[str, Any]:
    """
    Query Neo4j for the task DAG and identify which tasks can run in parallel.

    Args:
        feature_id:   The DAG ID written by write_dag (e.g. "dag-my-feature").
        neo4j_driver: Async Neo4j driver (graph_db.driver.Neo4jDriver); None → offline.

    Returns:
        {
            "all_tasks":        [{"id", "title", "status", "depends_on"}],
            "unblocked_tasks":  [{"id", "title"}],
            "parallel_groups":  [[task_ids_that_can_run_together], ...],
        }
    """
    if neo4j_driver is not None:
        try:
            result = await _query_neo4j(neo4j_driver, feature_id)
            if result:
                logger.info(
                    "[query_dag] Neo4j returned %d tasks for '%s'",
                    len(result.get("all_tasks", [])),
                    feature_id,
                )
                return result
        except Exception as exc:  # noqa: BLE001
            logger.warning("[query_dag] Neo4j query failed: %s", exc)

    return _stub(feature_id)


# --------------------------------------------------------------------------- #
# Real Neo4j query (delegates to driver helper)
# --------------------------------------------------------------------------- #
async def _query_neo4j(driver: Any, feature_id: str) -> Dict[str, Any]:
    """
    Use the driver's get_unblocked_tasks() to build the full DAG summary.
    The driver (graph_db/driver.py) returns {"unblocked": [...], "all": [...]}.
    """
    raw = await driver.get_unblocked_tasks(feature_id)

    all_tasks: List[Dict[str, Any]] = raw.get("all", [])
    unblocked: List[Dict[str, Any]] = raw.get("unblocked", [])

    parallel_groups = _compute_parallel_groups(all_tasks)

    return {
        "all_tasks": all_tasks,
        "unblocked_tasks": unblocked,
        "parallel_groups": parallel_groups,
    }


def _compute_parallel_groups(tasks: List[Dict[str, Any]]) -> List[List[str]]:
    """
    Topological level-sort: group tasks that have no dependency on each other.
    Each group can be executed in parallel.
    """
    id_to_task = {t["id"]: t for t in tasks}
    levels: Dict[str, int] = {}

    def depth(task_id: str, visiting: set) -> int:
        if task_id in levels:
            return levels[task_id]
        if task_id in visiting:
            return 0  # cycle guard
        visiting.add(task_id)
        task = id_to_task.get(task_id, {})
        deps = task.get("depends_on") or []
        d = 1 + max((depth(dep, visiting) for dep in deps if dep in id_to_task), default=0)
        levels[task_id] = d
        return d

    for t in tasks:
        depth(t["id"], set())

    if not levels:
        return []

    max_level = max(levels.values())
    groups: List[List[str]] = []
    for lvl in range(1, max_level + 1):
        group = [tid for tid, lv in levels.items() if lv == lvl]
        if group:
            groups.append(group)
    return groups


# --------------------------------------------------------------------------- #
# Offline stub
# --------------------------------------------------------------------------- #
def _stub(feature_id: str) -> Dict[str, Any]:
    """Return a plausible two-stage DAG for any feature_id."""
    logger.info("[query_dag] Offline stub for feature '%s'", feature_id)
    tasks: List[Dict[str, Any]] = [
        {
            "id": "setup",
            "title": "Environment & repository setup",
            "status": "pending",
            "depends_on": [],
        },
        {
            "id": "schema",
            "title": "Database schema & migrations",
            "status": "pending",
            "depends_on": ["setup"],
        },
        {
            "id": "api",
            "title": "Backend API implementation",
            "status": "pending",
            "depends_on": ["schema"],
        },
        {
            "id": "tests",
            "title": "Automated test suite",
            "status": "pending",
            "depends_on": ["api"],
        },
    ]
    return {
        "all_tasks": tasks,
        "unblocked_tasks": [{"id": "setup", "title": tasks[0]["title"]}],
        "parallel_groups": [["setup"], ["schema"], ["api"], ["tests"]],
    }
