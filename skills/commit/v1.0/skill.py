"""
commit skill — stage and commit generated files to git.

Live path:   subprocess git add + git commit; returns the real commit SHA.
Offline path: No-op stub returning a deterministic placeholder SHA when git
              is unavailable or repo_path is not a valid git repo.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


async def commit(
    message: str,
    files: List[str],
    repo_path: str = ".",
) -> Dict[str, Any]:
    """
    Stage ``files`` and create a git commit with ``message``.

    Args:
        message:   Commit message.
        files:     List of file paths (relative to ``repo_path``) to stage.
                   Pass ``["."]`` to stage all changes.
        repo_path: Root of the git repository (injected by AgentContext).

    Returns:
        {"commit_sha": str, "stub": bool}
    """
    if not message:
        message = "chore: auto-commit by Continuum"

    _files = files or ["."]
    _root = str(Path(repo_path).resolve())

    # Check that git is available and repo_path is a git repository
    if not _is_git_repo(_root):
        sha = _stub_sha(message + _root)
        logger.info("[commit] %s is not a git repo — offline stub sha=%s", _root, sha[:8])
        return {"commit_sha": sha, "stub": True}

    try:
        # Stage files
        add_rc, add_out = await _run(["git", "add", "--", *_files], cwd=_root)
        if add_rc != 0:
            logger.warning("[commit] git add failed: %s", add_out[:200])
            return {"commit_sha": _stub_sha(message), "stub": True}

        # Commit
        commit_rc, commit_out = await _run(
            ["git", "commit", "-m", message, "--allow-empty"],
            cwd=_root,
        )
        if commit_rc != 0:
            logger.warning("[commit] git commit failed: %s", commit_out[:200])
            return {"commit_sha": _stub_sha(message), "stub": True}

        # Get SHA of HEAD
        sha_rc, sha_out = await _run(["git", "rev-parse", "HEAD"], cwd=_root)
        sha = sha_out.strip() if sha_rc == 0 else _stub_sha(message)
        logger.info("[commit] committed sha=%s", sha[:8])
        return {"commit_sha": sha, "stub": False}

    except Exception as exc:  # noqa: BLE001
        logger.warning("[commit] git error: %s — offline stub", exc)
        return {"commit_sha": _stub_sha(message), "stub": True}


# ── Helpers ────────────────────────────────────────────────────────────────────
def _is_git_repo(path: str) -> bool:
    """True if ``path`` is inside a git repository."""
    return (Path(path) / ".git").exists() or _has_parent_git(path)


def _has_parent_git(path: str) -> bool:
    p = Path(path)
    for parent in [p] + list(p.parents):
        if (parent / ".git").exists():
            return True
    return False


async def _run(cmd: List[str], cwd: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    out = (stdout.decode("utf-8", errors="replace") + stderr.decode("utf-8", errors="replace")).strip()
    return proc.returncode or 0, out


def _stub_sha(seed: str) -> str:
    """Deterministic 40-char hex SHA for offline/stub commits."""
    return hashlib.sha1(seed.encode()).hexdigest()  # noqa: S324
