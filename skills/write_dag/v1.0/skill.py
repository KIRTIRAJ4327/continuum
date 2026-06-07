"""
write_dag skill — generates a task dependency DAG and writes it to Neo4j.

Live path:  Azure AI generates the task breakdown; Neo4j persists it.
Offline path: Derives tasks from the components list; skips Neo4j if None.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


async def write_dag(
    story: Dict[str, Any],
    contract: str,
    schema: str,
    components: List[Dict[str, Any]],
    neo4j_driver: Any = None,
) -> Dict[str, Any]:
    """
    Generate a task dependency DAG from the architecture and write to Neo4j.

    Args:
        story:        BSA story dict (title, description).
        contract:     OpenAPI YAML string.
        schema:       SQL DDL string.
        components:   List of {"name": str, "type": str, "responsibility": str}.
        neo4j_driver: Async Neo4j driver (from graph_db/driver.py); None → offline.

    Returns:
        {
            "dag_id":     str,
            "tasks":      [{"id": str, "title": str, "depends_on": [str], "component": str}],
            "task_count": int,
        }
    """
    title = story.get("title", "Feature")
    story_id = story.get("ticket", {}).get("story_id") or re.sub(r"[^a-z0-9]", "-", title.lower())[:20]
    dag_id = f"dag-{story_id}"

    # Try LLM for smarter task breakdown
    llm_tasks = await _azure_llm_tasks(title, story.get("description", ""), components)
    tasks = llm_tasks or _derive_tasks(components, title)

    dag: Dict[str, Any] = {
        "dag_id": dag_id,
        "tasks": tasks,
        "task_count": len(tasks),
    }

    # Persist to Neo4j if driver is available
    if neo4j_driver is not None:
        try:
            await neo4j_driver.write_dag(dag_id, tasks)
            dag["persisted"] = True
            logger.info("[write_dag] DAG '%s' (%d tasks) written to Neo4j", dag_id, len(tasks))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[write_dag] Neo4j write failed: %s", exc)
            dag["persisted"] = False
    else:
        dag["persisted"] = False
        logger.info("[write_dag] No Neo4j driver; DAG '%s' in-memory only", dag_id)

    return dag


# --------------------------------------------------------------------------- #
# Task derivation from components
# --------------------------------------------------------------------------- #
_COMPONENT_TASK_MAP = {
    "database": [
        ("db-schema",   "Apply database migrations",           []),
        ("db-seed",     "Seed initial / reference data",       ["db-schema"]),
    ],
    "backend": [
        ("api-models",  "Define data models & ORM mappings",   ["db-schema"]),
        ("api-routes",  "Implement REST API routes",           ["api-models"]),
        ("api-auth",    "Wire authentication middleware",      ["api-routes"]),
        ("api-tests",   "Write API integration tests",         ["api-routes"]),
    ],
    "frontend": [
        ("fe-scaffold", "Scaffold frontend application",       []),
        ("fe-api",      "Integrate frontend with API client",  ["api-routes", "fe-scaffold"]),
        ("fe-ui",       "Build UI components & pages",         ["fe-api"]),
        ("fe-tests",    "Write frontend unit tests",           ["fe-ui"]),
    ],
    "infra": [
        ("infra-iac",   "Provision cloud infrastructure (IaC)", []),
        ("infra-ci",    "Configure CI/CD pipeline",            ["infra-iac"]),
    ],
}

_DEFAULT_TASKS = [
    {"id": "setup",  "title": "Repository & environment setup", "depends_on": [],       "component": "infra"},
    {"id": "schema", "title": "Database schema & migrations",   "depends_on": ["setup"], "component": "database"},
    {"id": "api",    "title": "Backend API implementation",     "depends_on": ["schema"], "component": "backend"},
    {"id": "tests",  "title": "Automated test suite",           "depends_on": ["api"],   "component": "backend"},
]


def _derive_tasks(
    components: List[Dict[str, Any]], title: str
) -> List[Dict[str, Any]]:
    """Build a task list from the component types in the architecture."""
    tasks: List[Dict[str, Any]] = [
        {"id": "setup", "title": f"Environment setup for '{title}'", "depends_on": [], "component": "infra"},
    ]
    seen_ids = {"setup"}

    for comp in components:
        ctype = comp.get("type", "backend").lower()
        cname = comp.get("name", ctype)
        template_tasks = _COMPONENT_TASK_MAP.get(ctype, [])

        for task_id_base, task_title, base_deps in template_tasks:
            task_id = f"{cname}-{task_id_base}" if cname != ctype else task_id_base
            if task_id in seen_ids:
                continue
            # Resolve dependencies: keep only those that actually exist or are "setup"
            deps = []
            for d in base_deps:
                if d in seen_ids:
                    deps.append(d)
            if not deps:
                deps = ["setup"]
            tasks.append({
                "id": task_id,
                "title": task_title,
                "depends_on": deps,
                "component": cname,
            })
            seen_ids.add(task_id)

    return tasks if len(tasks) > 1 else _DEFAULT_TASKS


# --------------------------------------------------------------------------- #
# Azure AI helper — smarter task breakdown via LLM
# --------------------------------------------------------------------------- #
async def _azure_llm_tasks(
    title: str, description: str, components: List[Dict[str, Any]]
) -> Optional[List[Dict[str, Any]]]:
    api_key = os.getenv("AZURE_OPENAI_API_KEY", "")
    endpoint = (
        os.getenv("AZURE_AI_ENDPOINT") or os.getenv("AZURE_OPENAI_ENDPOINT") or ""
    ).rstrip("/")
    deployment = (
        os.getenv("AZURE_DEPLOYMENT_STRONG") or os.getenv("AZURE_DEPLOYMENT_ID") or ""
    )
    if not (api_key and endpoint and deployment):
        return None

    comp_summary = json.dumps(
        [{"name": c.get("name"), "type": c.get("type")} for c in components], indent=2
    )
    prompt = (
        f"Break down the following feature into engineering tasks:\n\n"
        f"Feature: {title}\n"
        f"Description: {description[:300]}\n"
        f"Components:\n{comp_summary}\n\n"
        "Return a JSON array where each item has:\n"
        '  { "id": "<short-slug>", "title": "<task title>", "depends_on": ["<id>", ...], "component": "<comp-name>" }\n\n'
        "Rules:\n"
        "- Include 6-12 tasks covering database, backend, frontend (if applicable), and testing.\n"
        "- Ensure depends_on only references IDs that appear earlier in the list.\n"
        "- Return ONLY the JSON array — no prose."
    )

    try:
        from langchain_azure_ai.chat_models import AzureChatCompletions  # type: ignore
        from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore

        model = AzureChatCompletions(
            azure_endpoint=endpoint,
            azure_deployment=deployment,
            api_key=api_key,
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
            max_tokens=2000,
        )
        resp = await model.ainvoke(
            [
                SystemMessage(content="You are a senior engineering lead. Return valid JSON only."),
                HumanMessage(content=prompt),
            ]
        )
        raw = (getattr(resp, "content", "") or "").strip()
        # Strip fences
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"```$", "", raw).strip()
        parsed = json.loads(raw)
        if isinstance(parsed, list) and parsed:
            return parsed
    except Exception as exc:  # noqa: BLE001
        logger.debug("[write_dag] LLM task breakdown skipped: %s", exc)
    return None
