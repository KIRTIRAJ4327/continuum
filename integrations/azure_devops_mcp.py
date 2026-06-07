"""
Azure DevOps REST API v7.1 client for Continuum.

Credential priority (highest to lowest):
  1. Explicit ``token`` kwarg to ``from_env()`` (injected via AgentContext).
  2. AZURE_DEVOPS_TOKEN environment variable.
  3. AZURE_DEVOPS_PAT environment variable (legacy alias).

If the minimum required vars (token + org + project) are absent every method
returns a stub dict with ``"stub": True`` so the offline/test path stays clean
and never crashes.

Usage:
    from integrations.azure_devops_mcp import get_ado_client
    client = get_ado_client()
    pr = await client.create_pull_request(...)
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)

_API_VERSION = "7.1"

# ── Module-level singleton ─────────────────────────────────────────────────────
_client_singleton: Optional["AzureDevOpsClient"] = None


def get_ado_client(token: str = "") -> "AzureDevOpsClient":
    """Return (or create) a module-level ADO client reused across calls."""
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = AzureDevOpsClient.from_env(token=token)
    return _client_singleton


# ── Helpers ────────────────────────────────────────────────────────────────────
def _stub_id(seed: str) -> int:
    """Deterministic stub integer ID for offline mode (MD5-keyed)."""
    return int(hashlib.md5(seed.encode()).hexdigest()[:8], 16)  # noqa: S324


def _b64_auth(token: str) -> str:
    return base64.b64encode(f":{token}".encode()).decode()


class AzureDevOpsClient:
    """
    Thin async wrapper around Azure DevOps REST API v7.1.

    Every method is safe to call without live credentials: it returns a stub
    dict with ``"stub": True`` when the client is not ready (no token / org).
    """

    def __init__(self, org_url: str, project: str, token: str):
        self.org_url = org_url.rstrip("/")
        self.project = project
        self.token = token

    @classmethod
    def from_env(cls, token: str = "") -> "AzureDevOpsClient":
        """Build a client from environment variables."""
        _token = (
            token
            or os.getenv("AZURE_DEVOPS_TOKEN", "")
            or os.getenv("AZURE_DEVOPS_PAT", "")
        )
        _org = (
            os.getenv("AZURE_DEVOPS_ORG_URL", "")
            or os.getenv("AZURE_DEVOPS_ORG", "")
        ).rstrip("/")
        _project = os.getenv("AZURE_DEVOPS_PROJECT", "")
        return cls(org_url=_org, project=_project, token=_token)

    # ── Internal helpers ───────────────────────────────────────────────────────
    def _ready(self) -> bool:
        return bool(self.token and self.org_url and self.project)

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Basic {_b64_auth(self.token)}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        }

    def _patch_headers(self) -> Dict[str, str]:
        h = self._headers()
        h["Content-Type"] = "application/json-patch+json"
        return h

    async def _http(self) -> Any:
        """Lazy-import httpx.AsyncClient factory. Raises if httpx missing."""
        try:
            import httpx  # type: ignore[import]
            return httpx.AsyncClient(timeout=30)
        except ImportError as exc:
            raise RuntimeError(
                "httpx is required for live ADO calls: pip install httpx"
            ) from exc

    # ── Work Items ─────────────────────────────────────────────────────────────
    async def create_work_item(
        self,
        work_item_type: str,
        title: str,
        description: str = "",
        tags: str = "continuum",
    ) -> Dict[str, Any]:
        """
        Create an ADO work item (e.g. ``User Story``, ``Task``, ``Bug``).

        Returns ``{"id": int, "url": str, "title": str, "stub": bool}``.
        """
        if not self._ready():
            sid = _stub_id(f"{work_item_type}-{title}-{time.time()}")
            logger.info("[ADO] offline stub — create_work_item type=%r title=%r", work_item_type, title)
            return {
                "id":    sid,
                "url":   f"https://dev.azure.com/stub/work-items/{sid}",
                "title": title,
                "stub":  True,
            }

        wi_type_enc = quote(work_item_type)
        url = (
            f"{self.org_url}/{self.project}/_apis/wit/workitems"
            f"/${wi_type_enc}?api-version={_API_VERSION}"
        )
        body = [
            {"op": "add", "path": "/fields/System.Title",       "value": title},
            {"op": "add", "path": "/fields/System.Description", "value": description},
            {"op": "add", "path": "/fields/System.Tags",         "value": tags},
        ]
        async with await self._http() as client:
            resp = await client.post(url, headers=self._patch_headers(), json=body)
            resp.raise_for_status()
            data = resp.json()

        return {
            "id":    data["id"],
            "url":   data.get("url", ""),
            "title": data["fields"].get("System.Title", title),
            "stub":  False,
        }

    async def update_work_item(
        self, item_id: int, state: str, comment: str = ""
    ) -> Dict[str, Any]:
        """Update a work item's state (e.g. ``Active`` → ``Resolved``)."""
        if not self._ready():
            return {"id": item_id, "state": state, "stub": True}

        url = (
            f"{self.org_url}/{self.project}/_apis/wit/workitems"
            f"/{item_id}?api-version={_API_VERSION}"
        )
        body: List[Dict[str, Any]] = [
            {"op": "add", "path": "/fields/System.State", "value": state},
        ]
        if comment:
            body.append({"op": "add", "path": "/fields/System.History", "value": comment})

        async with await self._http() as client:
            resp = await client.patch(url, headers=self._patch_headers(), json=body)
            resp.raise_for_status()
            data = resp.json()

        return {"id": data["id"], "state": state, "stub": False}

    # ── Pull Requests ──────────────────────────────────────────────────────────
    async def create_pull_request(
        self,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str = "",
        work_item_ids: Optional[List[int]] = None,
        repo: str = "",
    ) -> Dict[str, Any]:
        """
        Create a PR from ``source_branch`` → ``target_branch``.

        Returns ``{"id": int, "url": str, "status": str, "stub": bool}``.
        """
        if not self._ready():
            sid = _stub_id(f"pr-{source_branch}-{target_branch}-{time.time()}")
            logger.info("[ADO] offline stub — create_pull_request %r → %r", source_branch, target_branch)
            return {
                "id":     sid,
                "url":    f"https://dev.azure.com/stub/pullrequest/{sid}",
                "status": "active",
                "stub":   True,
            }

        _repo = repo or os.getenv("AZURE_DEVOPS_REPO", "")
        if not _repo:
            logger.warning("[ADO] AZURE_DEVOPS_REPO not set — returning PR stub")
            return {"id": 0, "url": "", "status": "active", "stub": True}

        url = (
            f"{self.org_url}/{self.project}/_apis/git/repositories"
            f"/{_repo}/pullrequests?api-version={_API_VERSION}"
        )
        body: Dict[str, Any] = {
            "sourceRefName": f"refs/heads/{source_branch}",
            "targetRefName": f"refs/heads/{target_branch}",
            "title":         title,
            "description":   description,
        }
        if work_item_ids:
            body["workItemRefs"] = [{"id": str(w)} for w in work_item_ids]

        async with await self._http() as client:
            resp = await client.post(url, headers=self._headers(), json=body)
            resp.raise_for_status()
            data = resp.json()

        pr_id = data["pullRequestId"]
        pr_url = f"{self.org_url}/{self.project}/_git/{_repo}/pullrequest/{pr_id}"
        return {"id": pr_id, "url": pr_url, "status": "active", "stub": False}

    async def get_pull_request_status(self, pr_id: int, repo: str = "") -> str:
        """Return PR status: ``active``, ``abandoned``, or ``completed``."""
        if not self._ready():
            return "active"

        _repo = repo or os.getenv("AZURE_DEVOPS_REPO", "")
        if not _repo:
            return "active"

        url = (
            f"{self.org_url}/{self.project}/_apis/git/repositories"
            f"/{_repo}/pullrequests/{pr_id}?api-version={_API_VERSION}"
        )
        async with await self._http() as client:
            resp = await client.get(url, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()

        return {1: "active", 2: "abandoned", 3: "completed"}.get(data.get("status", 1), "active")

    async def approve_pull_request(self, pr_id: int, repo: str = "") -> bool:
        """Vote to approve a PR (vote=10). Returns True on success."""
        if not self._ready():
            logger.info("[ADO] offline stub — approve_pull_request(%d)", pr_id)
            return False

        _repo = repo or os.getenv("AZURE_DEVOPS_REPO", "")
        if not _repo:
            return False

        # Resolve the authenticated user's descriptor first
        async with await self._http() as client:
            me_resp = await client.get(
                f"{self.org_url}/_apis/connectionData?api-version={_API_VERSION}",
                headers=self._headers(),
            )
            me_resp.raise_for_status()
            reviewer_id = (
                me_resp.json()
                .get("authenticatedUser", {})
                .get("subjectDescriptor", "")
            )

        if not reviewer_id:
            logger.warning("[ADO] Could not resolve reviewer descriptor — skipping vote")
            return False

        vote_url = (
            f"{self.org_url}/{self.project}/_apis/git/repositories"
            f"/{_repo}/pullrequests/{pr_id}/reviewers/{reviewer_id}"
            f"?api-version={_API_VERSION}"
        )
        async with await self._http() as client:
            resp = await client.put(
                vote_url,
                headers=self._headers(),
                json={"vote": 10, "isRequired": False},
            )
            resp.raise_for_status()

        return True

    async def add_pr_comment(
        self, pr_id: int, comment: str, repo: str = ""
    ) -> Dict[str, Any]:
        """Add a comment thread to a PR."""
        if not self._ready():
            return {"stub": True}

        _repo = repo or os.getenv("AZURE_DEVOPS_REPO", "")
        if not _repo:
            return {"stub": True}

        url = (
            f"{self.org_url}/{self.project}/_apis/git/repositories"
            f"/{_repo}/pullrequests/{pr_id}/threads?api-version={_API_VERSION}"
        )
        body = {
            "comments": [{"parentCommentId": 0, "content": comment, "commentType": 1}]
        }
        async with await self._http() as client:
            resp = await client.post(url, headers=self._headers(), json=body)
            resp.raise_for_status()
            return {**resp.json(), "stub": False}
