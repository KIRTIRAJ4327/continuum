# File: continuum/orchestrator/gates.py
"""
Gate logic — deterministic sensors (no model).

Gates are ground truth (PRD §4 Rule 2). They run lint, type, test, build, SAST,
and contract validation as subprocesses and return a (passed, output) tuple.
The orchestrator's _route() consults the resulting GateStatus to decide retry
vs. escalate.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Subprocess helper
# --------------------------------------------------------------------------- #
def _run(cmd: List[str], cwd: Optional[str] = None, timeout: int = 120) -> Tuple[int, str]:
    """Run a command, returning (returncode, combined_stdout_stderr)."""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return 127, f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s: {' '.join(cmd)}"
    out = (result.stdout or "") + (result.stderr or "")
    return result.returncode, out


def _have(cmd: str) -> bool:
    """True if a command is on PATH."""
    return shutil.which(cmd) is not None


# --------------------------------------------------------------------------- #
# Gate state helper (used by agent_runner when it records gate results)
# --------------------------------------------------------------------------- #
def update_gate_status(state, name: str, passed: bool, output: str) -> None:
    """
    Append/update the named gate on a ContinuumState.

    Imported lazily to keep this module free of state coupling for callers that
    just want to run a gate as a standalone check.
    """
    from .state import GateStatus  # local import: keep gates.py importable alone

    gate = next((g for g in state.gates if g.name == name), None)
    if gate is None:
        gate = GateStatus(name=name, status="pending")
        state.gates.append(gate)
    gate.status = "green" if passed else "red"
    gate.error_message = None if passed else output[:4000]
    gate.last_checked = time.time()


# --------------------------------------------------------------------------- #
# 1. local_verify — lint / type / test / build
# --------------------------------------------------------------------------- #
async def gate_local_verify(code_path: str) -> Tuple[bool, str]:
    """
    Run local verification — stop at the first failure.

    Order:
      1. ruff check
      2. mypy --ignore-missing-imports
      3. pytest tests/ -v --tb=short
      4. python -m py_compile <code_path>  (build sanity)

    Missing tools are treated as "skipped" (not a failure) so the gate still
    works on a thin dev environment. A clearly broken Python file still fails
    the build sub-step.

    Returns:
        (passed, combined_output)
    """
    parts: List[str] = []
    path = code_path or "."

    # 1. ruff
    if _have("ruff"):
        rc, out = _run(["ruff", "check", path])
        parts.append(f"[ruff] rc={rc}\n{out}")
        if rc != 0:
            return False, "\n".join(parts)
    else:
        parts.append("[ruff] skipped (not installed)")

    # 2. mypy
    if _have("mypy"):
        rc, out = _run(["mypy", path, "--ignore-missing-imports"])
        parts.append(f"[mypy] rc={rc}\n{out}")
        if rc != 0:
            return False, "\n".join(parts)
    else:
        parts.append("[mypy] skipped (not installed)")

    # 3. pytest
    if _have("pytest"):
        rc, out = _run(["pytest", "tests/", "-v", "--tb=short"], timeout=300)
        parts.append(f"[pytest] rc={rc}\n{out}")
        # rc==5 means "no tests collected" — treat as non-failure for M0
        if rc not in (0, 5):
            return False, "\n".join(parts)
    else:
        parts.append("[pytest] skipped (not installed)")

    # 4. build (py_compile)
    targets = _python_targets(path)
    if targets:
        rc, out = _run([sys.executable, "-m", "py_compile", *targets])
        parts.append(f"[py_compile] rc={rc} files={len(targets)}\n{out}")
        if rc != 0:
            return False, "\n".join(parts)
    else:
        parts.append("[py_compile] no python targets")

    return True, "\n".join(parts)


def _python_targets(path: str) -> List[str]:
    """Resolve `path` to a list of .py files to feed py_compile."""
    p = Path(path)
    if p.is_file() and p.suffix == ".py":
        return [str(p)]
    if p.is_dir():
        # Compile a small representative set; the whole tree is overkill for M0.
        return [str(f) for f in p.rglob("*.py")
                if "__pycache__" not in f.parts and ".venv" not in f.parts][:50]
    return []


# --------------------------------------------------------------------------- #
# 2. sast — bandit / semgrep / regex fallback
# --------------------------------------------------------------------------- #

# Patterns flagged by the regex fallback. Keys are short labels.
_SAST_PATTERNS = {
    "hardcoded_secret": re.compile(
        r"""(?ix)
        (password|passwd|pwd|api[_\s-]?key|secret|token)
        \s*[:=]\s*
        ['"][A-Za-z0-9_\-/+=]{8,}['"]
        """
    ),
    "eval_call":      re.compile(r"\beval\s*\("),
    "exec_call":      re.compile(r"\bexec\s*\("),
    "shell_true":     re.compile(r"shell\s*=\s*True"),
    "sql_concat":     re.compile(
        r"""(?ix)
        (execute|executemany|cursor\.execute)
        \s*\(
        \s*
        (?:f['"]|['"][^'"]*\s*\+|['"][^'"]*%\s)
        """
    ),
}


async def gate_sast(code_path: str) -> Tuple[bool, str]:
    """
    Run a SAST scan over the code path.

    Prefers `bandit`, then `semgrep`, then a small regex fallback. Returns the
    aggregated report. The gate is green when no issues are reported.
    """
    path = code_path or "."

    if _have("bandit"):
        rc, out = _run(["bandit", "-r", path, "-q", "-f", "txt"], timeout=120)
        # bandit exits non-zero when issues are found; the report is in stdout
        passed = rc == 0
        return passed, f"[bandit] rc={rc}\n{out}"

    if _have("semgrep"):
        rc, out = _run(
            ["semgrep", "--config=auto", "--quiet", "--error", path],
            timeout=180,
        )
        passed = rc == 0
        return passed, f"[semgrep] rc={rc}\n{out}"

    return _sast_regex_scan(path)


def _sast_regex_scan(path: str) -> Tuple[bool, str]:
    """Lightweight regex SAST — last-resort fallback when no scanner is present."""
    findings: List[str] = []
    root = Path(path)
    files: List[Path] = (
        [root] if root.is_file() else
        [f for f in root.rglob("*.py")
         if "__pycache__" not in f.parts and ".venv" not in f.parts]
    )

    for f in files[:500]:  # cap to keep the gate cheap
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for label, pat in _SAST_PATTERNS.items():
                if pat.search(line):
                    findings.append(f"{f}:{lineno}: {label}: {line.strip()[:120]}")

    if findings:
        return False, "[regex-sast] issues:\n" + "\n".join(findings[:50])
    return True, f"[regex-sast] clean (scanned {len(files)} files)"


# --------------------------------------------------------------------------- #
# 3. contract_validate — OpenAPI shape check
# --------------------------------------------------------------------------- #
async def gate_contract_validate(contract_yaml: str) -> Tuple[bool, str]:
    """
    Validate an OpenAPI 3.x contract structurally.

    M0 check (no jsonschema dep):
      * required top-level keys: openapi, info, paths
      * openapi starts with "3."
      * info has title + version
      * paths is a non-empty dict
    """
    if not contract_yaml or not contract_yaml.strip():
        return False, "contract is empty"

    try:
        doc = yaml.safe_load(contract_yaml)
    except yaml.YAMLError as exc:
        return False, f"invalid YAML: {exc}"

    if not isinstance(doc, dict):
        return False, "contract must be a YAML mapping at the top level"

    for key in ("openapi", "info", "paths"):
        if key not in doc:
            return False, f"missing required top-level key: '{key}'"

    version = str(doc.get("openapi", ""))
    if not version.startswith("3."):
        return False, f"openapi version must start with '3.', got '{version}'"

    info = doc.get("info") or {}
    if not isinstance(info, dict):
        return False, "'info' must be a mapping"
    for key in ("title", "version"):
        if not info.get(key):
            return False, f"info.{key} is required"

    paths = doc.get("paths")
    if not isinstance(paths, dict) or not paths:
        return False, "'paths' must be a non-empty mapping"

    return True, f"contract OK: openapi={version}, paths={len(paths)}"


# --------------------------------------------------------------------------- #
# 4. scope_conformance — M7 Mapping Fidelity (D11 Scope-Guard)
# --------------------------------------------------------------------------- #

# Matches short ALL-CAPS codes (2-10 chars) used as string literals in mapping /
# dict-key / assignment positions: "BR":  'ACC':  "CURR" =  "SV",
_SCOPE_KEY_RE = re.compile(r"""["']([A-Z][A-Z0-9_]{1,9})["']\s*[=:,]""")


def _check_scope(
    business_mappings: List[Dict],
    code: Dict[str, str],
) -> Dict[str, Any]:
    """Pure scanning logic — no network, no IO. Used by gate_scope_conformance."""
    if not business_mappings:
        return {
            "supplied": [],
            "found": [],
            "extra_in_code": [],
            "missing_in_code": [],
            "exact_match": True,
        }
    supplied: set = {m["code"] for m in business_mappings if m.get("code")}
    all_text = "\n".join((code or {}).values())
    found_in_code: set = {m.group(1) for m in _SCOPE_KEY_RE.finditer(all_text)}
    found = supplied & found_in_code
    missing = supplied - found_in_code
    extra = found_in_code - supplied
    return {
        "supplied": sorted(supplied),
        "found": sorted(found),
        "extra_in_code": sorted(extra),
        "missing_in_code": sorted(missing),
        "exact_match": len(missing) == 0 and len(extra) == 0,
    }


async def gate_scope_conformance(state: Any) -> Tuple[bool, str]:
    """
    M7: Scope-Guard gate. Checks that generated code uses *exactly* the supplied
    business_mappings — all supplied codes must appear, and no unrecognised
    mapping-like codes may be present.

    Side-effect: sets state.mapping_fidelity with the full scan result so the
    Evidence Stack (layer 4) and the MappingFidelity UI component can surface it.

    Gate is always green when state.business_mappings is empty (nothing to enforce).
    Never auto-retries on red — the run lands in `blocked` state (M6 Blocked panel).
    """
    mappings: List[Dict] = getattr(state, "business_mappings", []) or []
    if not mappings:
        return True, "no business_mappings supplied — scope guard skipped"

    code: Dict[str, str] = getattr(state, "code", {}) or {}
    result = _check_scope(mappings, code)
    state.mapping_fidelity = result

    if result["exact_match"]:
        n = len(result["supplied"])
        return True, f"scope ok: {n} mapping(s) confirmed in generated code"

    parts: List[str] = []
    if result["missing_in_code"]:
        parts.append("missing: " + ", ".join(result["missing_in_code"]))
    if result["extra_in_code"]:
        parts.append("extra: " + ", ".join(result["extra_in_code"]))
    return False, "scope mismatch — " + "; ".join(parts)
