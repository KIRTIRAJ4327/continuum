"""
create_story skill — creates a User Story in Azure DevOps (REST v7.1).

Live path:  AZURE_DEVOPS_ORG + AZURE_DEVOPS_PROJECT + ado_token  → POST to ADO.
Offline path: deterministic stub keyed by MD5 of the request.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any, Dict

logger = logging.getLogger(__name__)


async def create_story(request: str, jira_token: str = "") -> Dict[str, Any]:
    """
    Create a User Story in Azure DevOps and return its ID + URL.

    Args:
        request:    Plain-English feature description (becomes the Story title).
        jira_token: Azure DevOps PAT or Jira token (injected by AgentContext).

    Returns:
        {
            "story_id": str,
            "story_url": str,
            "title": str,
            "status": "created" | "stub",
        }
    """
    org = os.getenv("AZURE_DEVOPS_ORG", "")
    project = os.getenv("AZURE_DEVOPS_PROJECT", "")
    title = request.strip()[:200]

    if jira_token and org and project:
        result = await _ado_create(org, project, jira_token, title, request)
        if result:
            return result

    return _stub(title)


# --------------------------------------------------------------------------- #
# Real Azure DevOps REST call
# --------------------------------------------------------------------------- #
async def _ado_create(
    org: str, project: str, token: str, title: str, description: str
) -> Dict[str, Any] | None:
    """POST a User Story to ADO; returns None on any error."""
    try:
        import httpx
    except ImportError:
        logger.warning("[create_story] httpx not installed; falling back to stub")
        return None

    url = (
        f"https://dev.azure.com/{org}/{project}/_apis/wit/workitems"
        f"/$User%20Story?api-version=7.1"
    )
    patch_body = [
        {"op": "add", "path": "/fields/System.Title", "value": title},
        {"op": "add", "path": "/fields/System.Description", "value": description},
        {"op": "add", "path": "/fields/System.Tags", "value": "continuum;auto-generated"},
    ]
    headers = {
        "Content-Type": "application/json-patch+json",
        "Accept": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                url,
                content=json.dumps(patch_body),
                headers=headers,
                auth=("", token),
            )
        if resp.status_code in (200, 201):
            data = resp.json()
            work_id = data.get("id", "?")
            html_url = (
                data.get("_links", {}).get("html", {}).get("href")
                or f"https://dev.azure.com/{org}/{project}/_workitems/edit/{work_id}"
            )
            logger.info("[create_story] ADO work item created: %s", work_id)
            return {
                "story_id": str(work_id),
                "story_url": html_url,
                "title": title,
                "status": "created",
            }
        logger.warning(
            "[create_story] ADO returned %d: %s", resp.status_code, resp.text[:200]
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[create_story] ADO call failed: %s", exc)

    return None


# --------------------------------------------------------------------------- #
# Deterministic stub (offline / no credentials)
# --------------------------------------------------------------------------- #
def _stub(title: str) -> Dict[str, Any]:
    short_hash = hashlib.md5(title.encode()).hexdigest()[:8].upper()
    story_id = f"CONT-{short_hash}"
    logger.info("[create_story] Offline stub: %s", story_id)
    return {
        "story_id": story_id,
        "story_url": f"https://example.com/stories/{story_id}",
        "title": title,
        "status": "stub",
    }
