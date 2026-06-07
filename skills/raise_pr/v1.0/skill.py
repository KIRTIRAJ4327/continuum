"""
raise_pr skill — create a pull request in Azure DevOps.

Live path:   Delegates to integrations.azure_devops_mcp.AzureDevOpsClient.
Offline path: Deterministic stub with a well-formed URL when credentials absent.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict

logger = logging.getLogger(__name__)


async def raise_pr(
    source_branch: str,
    target_branch: str = "main",
    title: str = "Continuum auto-PR",
    description: str = "",
    ado_token: str = "",
) -> Dict[str, Any]:
    """
    Create a pull request in Azure DevOps.

    Args:
        source_branch: Feature branch to merge from.
        target_branch: Target branch (default ``main``).
        title:         PR title.
        description:   PR body / description.
        ado_token:     ADO PAT injected by AgentContext.

    Returns:
        {"pr_id": int, "pr_url": str, "stub": bool}
    """
    _branch = source_branch or os.getenv("FEATURE_BRANCH", "feature/continuum-auto")

    try:
        from integrations.azure_devops_mcp import AzureDevOpsClient  # type: ignore

        client = AzureDevOpsClient.from_env(token=ado_token)
        result = await client.create_pull_request(
            source_branch=_branch,
            target_branch=target_branch,
            title=title,
            description=description,
        )
        logger.info("[raise_pr] PR id=%s stub=%s", result["id"], result.get("stub"))
        return {
            "pr_id":  result["id"],
            "pr_url": result["url"],
            "stub":   result.get("stub", False),
        }

    except Exception as exc:  # noqa: BLE001
        logger.warning("[raise_pr] ADO call failed (%s) — offline stub", exc)

    # Offline / fallback stub
    org  = os.getenv("AZURE_DEVOPS_ORG_URL") or os.getenv("AZURE_DEVOPS_ORG") or ""
    proj = os.getenv("AZURE_DEVOPS_PROJECT", "")
    repo = os.getenv("AZURE_DEVOPS_REPO", "")
    url  = (
        f"{org.rstrip('/')}/{proj}/_git/{repo}/pullrequest/0"
        if org and proj and repo
        else "https://dev.azure.com/stub/pullrequest/0"
    )
    return {"pr_id": 0, "pr_url": url, "stub": True}
