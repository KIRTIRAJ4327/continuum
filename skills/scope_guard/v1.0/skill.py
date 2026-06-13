"""
Scope-Guard skill (M7 D11): verify generated code uses exactly the supplied
business_mappings — no missing codes, no invented extras.

Loaded via importlib.util.spec_from_file_location (never dotted imports).
Offline-safe: pure Python, no network, no external deps.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# Matches short ALL-CAPS codes (2-10 chars) that appear as string literals in
# dict-key / mapping / assignment positions:
#   "BR":    'ACC':    "CURR" =    "DB",
_MAPPING_KEY_RE = re.compile(r"""["']([A-Z][A-Z0-9_]{1,9})["']\s*[=:,]""")


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
) -> Dict[str, Any]:
    """
    Offline-safe scope conformance check.

    Scans generated code for mapping-like string literals (dict keys, enum values,
    constants) that match the supplied business-mapping codes.

    Returns:
      supplied        — codes provided at intent time
      found           — supplied codes confirmed in generated code
      extra_in_code   — code literals that look like codes but weren't supplied
      missing_in_code — supplied codes absent from generated code
      exact_match     — True iff missing and extra are both empty
    """
    return _check_scope(business_mappings, code or {})
