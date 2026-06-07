"""
approve_pr skill — creates and/or approves a pull request in Azure DevOps.

Live path:  Uses integrations.azure_devops_mcp.AzureDevOpsClient to:
              1. Create a PR from the feature branch to the target branch.
              2. Post the PR review summary as a PR comment.
              3. Vote 'approve' (vote=10) as the pipeline service account.
Offline path: Returns a stub PR dict with stub=True.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict

logger = logging.getLogger(__name__)


async def approve_pr(
    pr_title: str,
    pr_body: str,
    source_branch: str = "",
    target_branch: str = "main",
    ado_token: str = "",
    work_item_id: int = 0,
) -> Dict[str, Any]:
    """
    Create a PR in Azure DevOps and vote to approve it.

    Args:
        pr_title:      Pull request title.
        pr_body:       Markdown body / description.
        source_branch: Feature branch name (defaults to FEATURE_BRANCH env var).
        target_branch: Target branch (default "main").
        ado_token:     Azure DevOps PAT (injected by AgentContext).
        work_item_id:  Optional ADO work item ID to link to the PR.

    Returns:
        {
            "pr_id":     int,
            "pr_url":    str,
            "approved":  bool,
            "stub":      bool,
        }
    """
    branch = source_branch or os.getenv("FEATURE_BRANCH", "feature/continuum-auto")

    try:
        from integrations.azure_devops_mcp import AzureDevOpsClient  # type: ignore
        client = AzureDevOpsClient.from_env(token=ado_token)

        # Create the PR
        pr = await client.create_pull_request(
            source_branch=branch,
            target_branch=target_branch,
            title=pr_title,
            description=pr_body,
            work_item_ids=[work_item_id] if work_item_id else None,
        )

        if pr.get("stub"):
            logger.info("[approve_pr] ADO not configured — stub PR returned")
            return {**pr, "approved": False}

        pr_id = pr["id"]

        # Post the review summary as a comment
        if pr_body:
            await client.add_pr_comment(pr_id, f"**Continuum Auto-Review:**\n\n{pr_body[:2000]}")

        # Vote to approve
        approved = await client.approve_pull_request(pr_id)

        logger.info("[approve_pr] PR #%s created; approved=%s", pr_id, approved)
        return {
            "pr_id":    pr_id,
            "pr_url":   pr.get("url", ""),
            "approved": approved,
            "stub":     False,
        }

    except ImportError:
        logger.warning("[approve_pr] integrations.azure_devops_mcp not importable — stub")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[approve_pr] ADO call failed: %s", exc)

    # Offline / fallback stub
    return {
        "pr_id":    0,
        "pr_url":   "",
        "approved": False,
        "stub":     True,
    }
