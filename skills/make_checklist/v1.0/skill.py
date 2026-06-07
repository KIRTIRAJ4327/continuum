# File: continuum/skills/make_checklist/v1.0/skill.py

"""
Make checklist skill — generates a task checklist from the DAG.
"""
from typing import Dict, Any, List

async def make_checklist(dag: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate an ordered task checklist from the DAG.
    
    Args:
        dag: DAG dict from query_dag skill (all_tasks, unblocked_tasks, parallel_groups)
    
    Returns:
        {
            "checklist": [
                {
                    "id": str,
                    "title": str,
                    "order": int,
                    "effort": str (low/medium/high),
                    "parallel_with": [str]
                }
            ],
            "critical_path": [str],
            "estimated_total_effort": str
        }
    """
    # TODO: Order tasks by dependency + estimate effort
    # TODO: Identify critical path
    return {
        "checklist": [
            {
                "id": "auth",
                "title": "Build auth",
                "order": 1,
                "effort": "medium",
                "parallel_with": []
            },
            {
                "id": "dashboard",
                "title": "Build dashboard",
                "order": 2,
                "effort": "high",
                "parallel_with": []
            }
        ],
        "critical_path": ["auth", "dashboard"],
        "estimated_total_effort": "high"
    }
