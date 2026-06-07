"""
Azure DevOps REST API client — used by Continuum skills that need to interact
with ADO work items, repositories, and pull requests.

Credential priority:
  1. Explicit `token` arg to the constructor (for skills that inject ado_token).
  2. AZURE_DEVOPS_TOKEN environment variable.
  3. AZURE_DEVOPS_PAT environment variable (legacy alias).

If no token is available, every method returns a stub dict with "stub": True
so the offline path stays clean and testable.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_API_VERSION = "7.1"


class AzureDevOpsClient:
    """
    Thin async wrapper around the Azure DevOps REST API v7.1.

    Usage:
        client = AzureDevOpsClient.from_env()
        item   = await client.create_work_item("User Story", "My feature", "Description")
        pr     = await client.create_pull_request("feature/x", "main", "My PR", "Body")
        status = await client.get_pull_request_status(pr["id"])
    """

    def __init__(self, org_url: str, project: str, token: str):
        self.org_url  = org_url.rstrip("/")
        self.project  = project
        self._token   = token
        self._base    = f"{self.org_url}/{project}/_apis"
        self._git_base = f"{self.org_url}/{project}/_apis/git"

    # ------------------------------------------------------------------ #
    # Factory
    # ------------------------------------------------------------------ #
    @classmethod
    def from_env(cls, token: str = "") -> "AzureDevOpsClient":
        """Build a client from environment variables."""
        org  = os.getenv("AZURE_DEVOPS_ORG", "")
        proj = os.getenv("AZURE_DEVOPS_PROJECT", "")
        tok  = (
            token
            or os.getenv("AZURE_DEVOPS_TOKEN", "")
            or os.getenv("AZURE_DEVOPS_PAT", "")
        )
        if not org:
            logger.debug("[ADO] AZURE_DEVOPS_ORG not set; ADO calls will be stubs")
        base_url = f"https://dev.azure.com/{org}" if org else ""
        return cls(base_url, proj, tok)

    # ------------------------------------------------------------------ #
    # Work items
    # ------------------------------------------------------------------ #
    async def create_work_item(
        self,
        work_item_type: str,
        title: str,
        description: str,
        tags: str = "continuum;auto-generated",
        area_path: str = "",
    ) -> Dict[str, Any]:
        """
        Create a work item (User Story, Bug, Task, …) in ADO.

        Returns:
            {"id": int, "url": str, "title": str, "type": str, "stub": bool}
        """
        if not self._ready():
            return self._stub_wi(work_item_type, title)

        encoded_type = work_item_type.replace(" ", "%20")
        url = f"{self._base}/wit/workitems/${encoded_type}?api-version={_API_VERSION}"

        patch: List[Dict[str, Any]] = [
            {"op": "add", "path": "/fields/System.Title",       "value": title},
            {"op": "add", "path": "/fields/System.Description", "value": description},
            {"op": "add", "path": "/fields/System.Tags",        "value": tags},
        ]
        if area_path:
            patch.append({"op": "add", "path": "/fields/System.AreaPath", "value": area_path})

        try:
            import httpx
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    url,
                    content=json.dumps(patch),
                    headers={
                        "Content-Type": "application/json-patch+json",
                        "Accept": "application/json",
                    },
                    auth=("", self._token),
                )
            if resp.status_code in (200, 201):
                data = resp.json()
                wi_id = data["id"]
                html  = (
                    data.get("_links", {}).get("html", {}).get("href")
                    or f"{self.org_url}/{self.project}/_workitems/edit/{wi_id}"
                )
                logger.info("[ADO] Work item created: %s #%s", work_item_type, wi_id)
                return {"id": wi_id, "url": html, "title": title, "type": work_item_type, "stub": False}

            logger.warning("[ADO] create_work_item HTTP %d: %s", resp.status_code, resp.text[:200])
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ADO] create_work_item failed: %s", exc)

        return self._stub_wi(work_item_type, title)

    async def update_work_item(
        self, item_id: int, state_value: str = "Active", comment: str = ""
    ) -> Dict[str, Any]:
        """Update the state of an existing work item."""
        if not self._ready():
            return {"id": item_id, "state": state_value, "stub": True}

        url = f"{self._base}/wit/workitems/{item_id}?api-version={_API_VERSION}"
        patch: List[Dict[str, Any]] = [
            {"op": "add", "path": "/fields/System.State", "value": state_value},
        ]
        if comment:
            patch.append({"op": "add", "path": "/fields/System.History", "value": comment})

        try:
            import httpx
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.patch(
                    url,
                    content=json.dumps(patch),
                    headers={
                        "Content-Type": "application/json-patch+json",
                        "Accept": "application/json",
                    },
                    auth=("", self._token),
                )
            if resp.status_code == 200:
                return {"id": item_id, "state": state_value, "stub": False}
            logger.warning("[ADO] update_work_item HTTP %d", resp.status_code)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ADO] update_work_item failed: %s", exc)

        return {"id": item_id, "state": state_value, "stub": True}

    # ------------------------------------------------------------------ #
    # Pull requests
    # ------------------------------------------------------------------ #
    async def create_pull_request(
        self,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str,
        repository: str = "",
        auto_complete: bool = False,
        work_item_ids: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        """
        Open a pull request in the default (or named) ADO git repository.

        Returns:
            {"id": int, "url": str, "title": str, "status": str, "stub": bool}
        """
        if not self._ready():
            return self._stub_pr(title)

        repo = repository or os.getenv("AZURE_DEVOPS_REPO", "")
        if not repo:
            logger.warning("[ADO] AZURE_DEVOPS_REPO not set; cannot create PR")
            return self._stub_pr(title)

        url = f"{self._git_base}/repositories/{repo}/pullrequests?api-version={_API_VERSION}"
        body: Dict[str, Any] = {
            "title": title,
            "description": description,
            "sourceRefName": _ref(source_branch),
            "targetRefName": _ref(target_branch),
        }
        if auto_complete:
            body["completionOptions"] = {"mergeStrategy": "squash"}
        if work_item_ids:
            body["workItemRefs"] = [{"id": str(i)} for i in work_item_ids]

        try:
            import httpx
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    url,
                    content=json.dumps(body),
                    headers={"Content-Type": "application/json", "Accept": "application/json"},
                    auth=("", self._token),
                )
            if resp.status_code in (200, 201):
                data = resp.json()
                pr_id = data["pullRequestId"]
                pr_url = (
                    f"{self.org_url}/{self.project}/_git/{repo}/pullrequest/{pr_id}"
                )
                logger.info("[ADO] PR created: #%s '%s'", pr_id, title)
                return {"id": pr_id, "url": pr_url, "title": title, "status": "active", "stub": False}

            logger.warning("[ADO] create_pull_request HTTP %d: %s", resp.status_code, resp.text[:200])
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ADO] create_pull_request failed: %s", exc)

        return self._stub_pr(title)

    async def get_pull_request_status(self, pr_id: int, repository: str = "") -> str:
        """
        Get the current status of a PR.

        Returns one of: "draft", "active", "abandoned", "completed".
        """
        if not self._ready():
            return "active"  # optimistic stub

        repo = repository or os.getenv("AZURE_DEVOPS_REPO", "")
        if not repo:
            return "active"

        url = f"{self._git_base}/repositories/{repo}/pullrequests/{pr_id}?api-version={_API_VERSION}"
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, auth=("", self._token))
            if resp.status_code == 200:
                status_map = {1: "active", 2: "abandoned", 3: "completed"}
                raw_status = resp.json().get("status", 1)
                return status_map.get(raw_status, "active")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ADO] get_pull_request_status failed: %s", exc)
        return "active"

    async def approve_pull_request(self, pr_id: int, repository: str = "") -> bool:
        """
        Vote 'approve' (vote=10) on a PR as the authenticated user.

        Returns True on success.
        """
        if not self._ready():
            return False

        repo = repository or os.getenv("AZURE_DEVOPS_REPO", "")
        if not repo:
            return False

        # Get the current user's descriptor first
        me_url = f"{self.org_url.replace('dev.azure.com', 'vssps.dev.azure.com')}/_apis/profile/profiles/me?api-version={_API_VERSION}"
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                me_resp = await client.get(me_url, auth=("", self._token))
                if me_resp.status_code != 200:
                    return False
                reviewer_id = me_resp.json().get("id", "")

                vote_url = (
                    f"{self._git_base}/repositories/{repo}/pullrequests/{pr_id}"
                    f"/reviewers/{reviewer_id}?api-version={_API_VERSION}"
                )
                vote_resp = await client.put(
                    vote_url,
                    content=json.dumps({"vote": 10}),  # 10 = approve
                    headers={"Content-Type": "application/json"},
                    auth=("", self._token),
                )
                return vote_resp.status_code in (200, 201)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ADO] approve_pull_request failed: %s", exc)
        return False

    async def add_pr_comment(
        self, pr_id: int, comment: str, repository: str = ""
    ) -> bool:
        """Post a top-level comment on a pull request."""
        if not self._ready():
            return False

        repo = repository or os.getenv("AZURE_DEVOPS_REPO", "")
        if not repo:
            return False

        url = (
            f"{self._git_base}/repositories/{repo}/pullrequests/{pr_id}"
            f"/threads?api-version={_API_VERSION}"
        )
        body = {"comments": [{"parentCommentId": 0, "content": comment, "commentType": 1}], "status": 1}
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    url,
                    content=json.dumps(body),
                    headers={"Content-Type": "application/json"},
                    auth=("", self._token),
                )
            return resp.status_code in (200, 201)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ADO] add_pr_comment failed: %s", exc)
        return False

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _ready(self) -> bool:
        """True if enough config is present to attempt a real ADO call."""
        return bool(self._token and self.org_url and self.project)

    @staticmethod
    def _stub_wi(work_item_type: str, title: str) -> Dict[str, Any]:
        import hashlib
        h = hashlib.md5(title.encode()).hexdigest()[:6].upper()
        return {
            "id": int(h, 16) % 10_000 or 1,
            "url": f"https://example.com/workitems/{h}",
            "title": title,
            "type": work_item_type,
            "stub": True,
        }

    @staticmethod
    def _stub_pr(title: str) -> Dict[str, Any]:
        import hashlib
        h = hashlib.md5(title.encode()).hexdigest()[:6].upper()
        return {
            "id": int(h, 16) % 10_000 or 1,
            "url": f"https://example.com/pullrequest/{h}",
            "title": title,
            "status": "active",
            "stub": True,
        }


# --------------------------------------------------------------------------- #
# Convenience singleton accessor for skills
# --------------------------------------------------------------------------- #
_client: Optional[AzureDevOpsClient] = None


def get_ado_client(token: str = "") -> AzureDevOpsClient:
    """Return a module-level ADO client (one per process)."""
    global _client
    if _client is None or token:
        _client = AzureDevOpsClient.from_env(token)
    return _client


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _ref(branch: str) -> str:
    """Normalise a branch name to a full git ref."""
    if branch.startswith("refs/"):
        return branch
    return f"refs/heads/{branch}"
