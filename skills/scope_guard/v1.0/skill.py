"""
Scope-Guard skill (M7 D11): verify generated code uses exactly the supplied
business_mappings — no missing codes, no invented extras.

Loaded via importlib.util.spec_from_file_location (never dotted imports).
Offline-safe: pure Python, no network, no external deps.

P0.2: when a BoxLite sandbox is injected, reads real files from the workdir
instead of scanning the in-memory code dict. Fallback to text scan unchanged.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

# Matches short ALL-CAPS codes (2-10 chars) that appear as string literals in
# dict-key / mapping / assignment positions:
#   "BR":    'ACC':    "CURR" =    "DB",
_MAPPING_KEY_RE = re.compile(r"""["']([A-Z][A-Z0-9_]{1,9})["']\s*[=:,]""")

# Source file extensions to scan when reading from a live box workdir.
_SOURCE_EXTS = {".py", ".ts", ".js", ".java", ".html", ".css", ".json"}


def _discover_source_files(workdir: Path) -> List[Path]:
    """Walk workdir, returning source files (skipping .git / __pycache__)."""
    results = []
    for p in workdir.rglob("*"):
        if p.is_file() and p.suffix in _SOURCE_EXTS:
            # Skip git internals and pycache
            parts = p.parts
            if ".git" in parts or "__pycache__" in parts:
                continue
            results.append(p)
    return results


def _check_scope(
    business_mappings: List[Dict[str, str]],
    code: Dict[str, str],
) -> Dict[str, Any]:
    """Pure scanning logic. Returns the full fidelity result dict."""
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
    found_in_code: set = {m.group(1) for m in _MAPPING_KEY_RE.finditer(all_text)}

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


async def scope_guard(
    business_mappings: List[Dict[str, str]],
    code: Optional[Dict[str, str]] = None,
    sandbox: Optional[Any] = None,  # P0.2: injected BoxLite (already in _INJECTED_PARAMS)
) -> Dict[str, Any]:
    """
    Offline-safe scope conformance check.

    P0.2: when `sandbox` is present (BoxLite), reads real files from the workdir
    instead of scanning the in-memory code dict — making "exact_match" a claim
    about actual source files, not a text string.

    Returns:
      supplied        — codes provided at intent time
      found           — supplied codes confirmed in generated code
      extra_in_code   — code literals that look like codes but weren't supplied
      missing_in_code — supplied codes absent from generated code
      exact_match     — True iff missing and extra are both empty
    """
    if sandbox is not None and hasattr(sandbox, "workdir"):
        # P0.2: read real files from the box workdir
        real_code: Dict[str, str] = {}
        for fpath in _discover_source_files(sandbox.workdir):
            try:
                rel = str(fpath.relative_to(sandbox.workdir))
                real_code[rel] = await sandbox.read(rel)
            except Exception:  # noqa: BLE001
                pass
        if real_code:
            return _check_scope(business_mappings, real_code)
        # fallthrough if workdir was empty (no files written yet)

    return _check_scope(business_mappings, code or {})
