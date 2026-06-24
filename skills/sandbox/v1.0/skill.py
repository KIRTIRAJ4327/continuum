"""
Box Lite — P0.2 isolated execution for Tier-2 developer agents.

Provides a per-agent ephemeral workdir backed by the host filesystem.
Zero new dependencies: subprocess, asyncio, pathlib, shutil, time (all stdlib).
Fully offline-safe — importable without any external package.

Upgrade path: DockerBox / ACABox / HyperlightBox implement the same 5-method
interface so skills and gates never change when the backend is swapped.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_BASE = Path("/tmp/continuum")


@dataclass
class ExecResult:
    returncode: int    # -1 = timeout, never None
    stdout: str
    stderr: str
    duration_s: float

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def summary(self) -> str:
        if self.returncode == -1:
            tag = "TIMEOUT"
        elif self.ok:
            tag = "PASS"
        else:
            tag = "FAIL"
        return f"{tag} ({self.duration_s:.2f}s) rc={self.returncode}"


class BoxLite:
    """
    Ephemeral per-agent workdir.
    Created on __init__, destroyed on teardown().
    The workdir is a bare git repo so diff() works from the first file written.

    Same 5-method interface as DockerBox / ACABox / HyperlightBox:
      write(), exec(), diff(), read(), teardown()
    """

    def __init__(self, run_id: str, agent: str) -> None:
        self.run_id = run_id
        self.agent = agent
        self.workdir: Path = _BASE / run_id / agent
        self.workdir.mkdir(parents=True, exist_ok=True)

        # Bare git so diff() works from the first file written.
        _git_env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "continuum",
            "GIT_AUTHOR_EMAIL": "continuum@local",
            "GIT_COMMITTER_NAME": "continuum",
            "GIT_COMMITTER_EMAIL": "continuum@local",
        }
        try:
            import subprocess
            subprocess.run(
                ["git", "init", "-q"],
                cwd=self.workdir, check=True, capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "--allow-empty", "-m", "base", "-q"],
                cwd=self.workdir, check=True, capture_output=True,
                env=_git_env,
            )
        except Exception as exc:  # noqa: BLE001
            # git unavailable — diff() will fall back to listing files
            logger.debug("[BoxLite] git init skipped: %s", exc)
            self._git_ok = False
        else:
            self._git_ok = True

        logger.debug("[BoxLite] workdir=%s git_ok=%s", self.workdir, self._git_ok)

    # ── 5-method interface ────────────────────────────────────────────────── #

    async def write(self, path: str, content: str) -> None:
        """Write a file into the box. Parent dirs created automatically."""
        target = self.workdir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        logger.debug("[BoxLite] write %s (%d chars)", path, len(content))

    async def exec(self, cmd: str, timeout: int = 60) -> ExecResult:
        """
        Run a shell command inside the workdir.
        Returns ExecResult with returncode=-1 on timeout (process is killed).
        """
        start = time.monotonic()
        proc: Optional[asyncio.subprocess.Process] = None
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                cwd=str(self.workdir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            rc = proc.returncode if proc.returncode is not None else -1
            result = ExecResult(
                returncode=rc,
                stdout=stdout_b.decode("utf-8", errors="replace"),
                stderr=stderr_b.decode("utf-8", errors="replace"),
                duration_s=time.monotonic() - start,
            )
        except asyncio.TimeoutError:
            # Kill the subprocess so it doesn't become a zombie
            if proc is not None:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:  # noqa: BLE001
                    pass
            result = ExecResult(
                returncode=-1,
                stdout="",
                stderr=f"timeout after {timeout}s",
                duration_s=time.monotonic() - start,
            )
        logger.debug("[BoxLite] exec %r → %s", cmd[:80], result.summary())
        return result

    async def diff(self) -> str:
        """Real git diff of workdir against the base commit."""
        if self._git_ok:
            r = await self.exec("git add -A && git diff HEAD")
            return r.stdout or ""
        # Fallback when git unavailable: list written files
        files = [str(p.relative_to(self.workdir)) for p in self.workdir.rglob("*") if p.is_file()]
        return "\n".join(f"+ {f}" for f in sorted(files))

    async def read(self, path: str) -> str:
        """Read a file out of the box (for scope-guard, evidence logging)."""
        return (self.workdir / path).read_text(encoding="utf-8")

    async def teardown(self) -> None:
        """Destroy the workdir. Called after artifact extraction."""
        shutil.rmtree(self.workdir, ignore_errors=True)
        logger.debug("[BoxLite] torn down %s", self.workdir)

    # ── Convenience ──────────────────────────────────────────────────────── #

    async def run_suite(
        self,
        *,
        lint_cmd: Optional[str] = None,
        typecheck_cmd: Optional[str] = None,
        test_cmd: Optional[str] = None,
        timeout: int = 120,
    ) -> Dict[str, ExecResult]:
        """
        Run lint → typecheck → test in sequence.
        Returns {step: ExecResult} for evidence logging.
        Stops at the first failure (same contract as gate_local_verify).
        """
        results: Dict[str, ExecResult] = {}
        for step, cmd in [
            ("lint",      lint_cmd),
            ("typecheck", typecheck_cmd),
            ("test",      test_cmd),
        ]:
            if not cmd:
                continue
            r = await self.exec(cmd, timeout=timeout)
            results[step] = r
            if not r.ok:
                break
        return results
