"""
make_checklist skill — generates an ordered task checklist from the DAG.

Pure Python: topological sort + heuristic effort estimation.
No LLM or external dependency needed — deterministic and always offline-safe.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Keywords that map to effort tiers
_HIGH_KEYWORDS   = {"auth", "security", "oauth", "jwt", "encryption", "payment", "database", "schema", "migration"}
_MEDIUM_KEYWORDS = {"api", "route", "endpoint", "model", "integration", "service", "component", "ui", "page"}
_LOW_KEYWORDS    = {"setup", "scaffold", "seed", "config", "lint", "format", "doc", "readme"}


async def make_checklist(dag: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert a DAG dict (from query_dag) into a developer checklist.

    Args:
        dag: {
            "all_tasks":       [{"id", "title", "status", "depends_on"}],
            "unblocked_tasks": [...],
            "parallel_groups": [[ids], ...],
        }

    Returns:
        {
            "checklist":              [{"id", "title", "order", "effort", "parallel_with", "status"}],
            "critical_path":          [task_ids],
            "estimated_total_effort": "low" | "medium" | "high",
            "parallel_opportunity":   bool,
        }
    """
    all_tasks: List[Dict[str, Any]] = dag.get("all_tasks") or []
    groups: List[List[str]] = dag.get("parallel_groups") or []

    if not all_tasks:
        logger.warning("[make_checklist] DAG has no tasks — returning empty checklist")
        return {
            "checklist": [],
            "critical_path": [],
            "estimated_total_effort": "low",
            "parallel_opportunity": False,
        }

    # Build a lookup so we can annotate "parallel_with"
    group_of: Dict[str, List[str]] = {}
    for group in groups:
        for task_id in group:
            # parallel_with = all other tasks in the same group
            group_of[task_id] = [t for t in group if t != task_id]

    # Topological sort (Kahn's algorithm)
    sorted_tasks = _topo_sort(all_tasks)

    checklist: List[Dict[str, Any]] = []
    effort_counts = {"low": 0, "medium": 0, "high": 0}

    for order, task in enumerate(sorted_tasks, start=1):
        tid = task.get("id", f"task-{order}")
        title = task.get("title", tid)
        effort = _estimate_effort(title)
        effort_counts[effort] += 1

        checklist.append({
            "id": tid,
            "title": title,
            "order": order,
            "effort": effort,
            "parallel_with": group_of.get(tid, []),
            "status": task.get("status", "pending"),
        })

    critical_path = _find_critical_path(all_tasks)
    total_effort  = _roll_up_effort(effort_counts)
    has_parallel  = any(len(g) > 1 for g in groups)

    logger.info(
        "[make_checklist] %d tasks, critical_path=%s, effort=%s, parallel=%s",
        len(checklist), critical_path, total_effort, has_parallel,
    )

    return {
        "checklist": checklist,
        "critical_path": critical_path,
        "estimated_total_effort": total_effort,
        "parallel_opportunity": has_parallel,
    }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _estimate_effort(title: str) -> str:
    """Heuristic effort estimate based on keywords in the task title."""
    lower = title.lower()
    if any(kw in lower for kw in _HIGH_KEYWORDS):
        return "high"
    if any(kw in lower for kw in _MEDIUM_KEYWORDS):
        return "medium"
    return "low"


def _topo_sort(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Kahn's algorithm — stable topological sort."""
    id_to_task = {t["id"]: t for t in tasks}
    in_degree: Dict[str, int] = {t["id"]: 0 for t in tasks}
    dependents: Dict[str, List[str]] = {t["id"]: [] for t in tasks}

    for task in tasks:
        for dep in task.get("depends_on") or []:
            if dep in in_degree:
                in_degree[task["id"]] += 1
                dependents[dep].append(task["id"])

    queue = sorted(  # sort for determinism
        [tid for tid, deg in in_degree.items() if deg == 0]
    )
    result: List[Dict[str, Any]] = []

    while queue:
        tid = queue.pop(0)
        result.append(id_to_task[tid])
        for child in sorted(dependents.get(tid, [])):
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

    # Append any cycles (shouldn't happen in a valid DAG)
    seen = {t["id"] for t in result}
    result += [t for t in tasks if t["id"] not in seen]
    return result


def _find_critical_path(tasks: List[Dict[str, Any]]) -> List[str]:
    """
    Longest dependency chain (critical path) by task count.
    Returns the chain of IDs from root to the deepest leaf.
    """
    id_to_task = {t["id"]: t for t in tasks}
    memo: Dict[str, List[str]] = {}

    def longest(tid: str) -> List[str]:
        if tid in memo:
            return memo[tid]
        task = id_to_task.get(tid)
        if not task:
            return [tid]
        deps = task.get("depends_on") or []
        if not deps:
            memo[tid] = [tid]
            return [tid]
        best: List[str] = []
        for dep in deps:
            if dep in id_to_task:
                chain = longest(dep)
                if len(chain) > len(best):
                    best = chain
        path = best + [tid]
        memo[tid] = path
        return path

    best_path: List[str] = []
    for task in tasks:
        path = longest(task["id"])
        if len(path) > len(best_path):
            best_path = path
    return best_path


def _roll_up_effort(counts: Dict[str, int]) -> str:
    """Derive overall effort from per-task tallies."""
    if counts.get("high", 0) > 0:
        return "high"
    if counts.get("medium", 0) > 1:
        return "medium"
    return "low"
