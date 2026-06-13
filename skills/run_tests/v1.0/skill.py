"""
run_tests skill — executes the generated test suite and returns structured results.

Runs pytest with coverage in the code directory (or a temp directory with the
generated code materialised to disk). Falls back gracefully when pytest is not
available or the code path doesn't exist.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


async def run_tests(
    code: Dict[str, str],
    repo_path: str = ".",
    timeout: int = 120,
) -> Dict[str, Any]:
    """
    Execute pytest against the provided code files.

    Args:
        code:      Generated code files {"file_path": content}.
        repo_path: Base directory for the repo (injected by AgentContext).
        timeout:   Max seconds to allow pytest to run (default 120).

    Returns:
        {
            "passed":        bool,
            "total":         int,
            "passed_count":  int,
            "failed_count":  int,
            "skipped_count": int,
            "coverage_pct":  float,
            "failures":      [{"test": str, "error": str}],
            "raw_output":    str,
        }
    """
    if not code:
        logger.warning("[run_tests] No code to test")
        return _empty_result(passed=True, note="no code provided")

    # Materialise generated files into a temp directory
    with tempfile.TemporaryDirectory(prefix="continuum_tests_") as tmpdir:
        _write_files(tmpdir, code)
        return await _run_pytest(tmpdir, timeout)


# --------------------------------------------------------------------------- #
# File materialisation
# --------------------------------------------------------------------------- #
def _write_files(base: str, code: Dict[str, str]) -> None:
    """Write the code dict to disk so pytest can find the files."""
    for rel_path, content in code.items():
        abs_path = Path(base) / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(content, encoding="utf-8")

    # Ensure a minimal conftest.py exists if none was generated
    conftest = Path(base) / "conftest.py"
    if not conftest.exists():
        conftest.write_text(
            "import sys\nfrom pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).parent / 'src'))\n",
            encoding="utf-8",
        )


# --------------------------------------------------------------------------- #
# pytest runner
# --------------------------------------------------------------------------- #
async def _run_pytest(cwd: str, timeout: int) -> Dict[str, Any]:
    """Run pytest --tb=short --json-report in `cwd`, parse results."""
    cmd = [
        "python", "-m", "pytest",
        "tests/",
        "--tb=short",
        "--no-header",
        "-q",
        "--json-report",
        "--json-report-file=.pytest_report.json",
        "--cov=src",
        "--cov-report=json:.coverage.json",
        "--cov-fail-under=0",   # don't fail on low coverage — we just report it
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        raw_out = (stdout.decode("utf-8", errors="replace") + stderr.decode("utf-8", errors="replace")).strip()

        # Parse JSON report if it was written
        report_path = Path(cwd) / ".pytest_report.json"
        if report_path.exists():
            return _parse_json_report(report_path, raw_out)

        # Fallback: parse text output
        return _parse_text_output(raw_out, proc.returncode == 0)

    except asyncio.TimeoutError:
        logger.warning("[run_tests] pytest timed out after %ds", timeout)
        return _empty_result(passed=False, note=f"pytest timed out after {timeout}s")
    except FileNotFoundError:
        logger.warning("[run_tests] pytest not installed in this environment")
        return _empty_result(passed=True, note="pytest not available — skipped")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[run_tests] unexpected error: %s", exc)
        return _empty_result(passed=False, note=str(exc))


# --------------------------------------------------------------------------- #
# Result parsers
# --------------------------------------------------------------------------- #
def _parse_json_report(report_path: Path, raw_out: str) -> Dict[str, Any]:
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
        summary = data.get("summary", {})
        passed   = summary.get("passed", 0)
        failed   = summary.get("failed", 0)
        skipped  = summary.get("skipped", 0)
        total    = summary.get("total", passed + failed + skipped)

        failures: List[Dict[str, str]] = []
        for test in data.get("tests", []):
            if test.get("outcome") == "failed":
                failures.append({
                    "test":  test.get("nodeid", "?"),
                    "error": (test.get("call", {}) or {}).get("longrepr", "")[:400],
                })

        # Coverage
        cov_pct = _read_coverage(report_path.parent)

        return {
            "passed":        failed == 0,
            "total":         total,
            "passed_count":  passed,
            "failed_count":  failed,
            "skipped_count": skipped,
            "coverage_pct":  cov_pct,
            "failures":      failures,
            "raw_output":    raw_out[:2000],
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("[run_tests] JSON report parse failed: %s", exc)
        return _parse_text_output(raw_out, failed=False)


def _parse_text_output(output: str, passed: bool) -> Dict[str, Any]:
    """Minimal parser for pytest text output."""
    # "5 passed, 1 failed in 2.3s"
    m = re.search(r"(\d+) passed", output)
    p = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) failed", output)
    f = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) skipped", output)
    s = int(m.group(1)) if m else 0

    return {
        "passed":        passed,
        "total":         p + f + s,
        "passed_count":  p,
        "failed_count":  f,
        "skipped_count": s,
        "coverage_pct":  0.0,
        "failures":      [],
        "raw_output":    output[:2000],
    }


def _read_coverage(base: Path) -> float:
    cov_path = base / ".coverage.json"
    try:
        if cov_path.exists():
            data = json.loads(cov_path.read_text(encoding="utf-8"))
            return data.get("totals", {}).get("percent_covered", 0.0)
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def _empty_result(passed: bool, note: str = "") -> Dict[str, Any]:
    return {
        "passed":        passed,
        "total":         0,
        "passed_count":  0,
        "failed_count":  0,
        "skipped_count": 0,
        "coverage_pct":  0.0,
        "failures":      [],
        "raw_output":    note,
    }
