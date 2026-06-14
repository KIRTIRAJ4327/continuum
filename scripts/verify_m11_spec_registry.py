#!/usr/bin/env python
"""
verify_m11_spec_registry.py — M11 Spec Registry offline verification.

Proves the persistent, versioned spec store works with zero external services:

  1. Run 1 on a component creates a Spec node (version 1) in the in-memory store.
  2. Run 2 on the same component retrieves Run 1's spec *before* drafting — the
     cross-run grounding the BSA relies on (process-level store persists).
  3. Run 2 whose spec materially differs supersedes Run 1: a SUPERSEDES link with
     a recorded reason, the old version flips to status='superseded', and the
     version chain returned by GET /specs is [v1(superseded), v2(current)].
  4. The scope-guard gate checks conformance to the Registry spec — undeclared
     drift → red; the same drift *with* a recorded supersession → green.

Usage:
  python scripts/verify_m11_spec_registry.py
Expected output:
  4/4 checks passed
"""
from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graph_db import spec_registry  # noqa: E402
from orchestrator.component import component_slug  # noqa: E402
from orchestrator.gates import gate_scope_conformance  # noqa: E402
from orchestrator.state import ContinuumState  # noqa: E402
from importlib.util import spec_from_file_location, module_from_spec  # noqa: E402

CHECKS: list = []

# Two specs for the same component that materially differ (different endpoints).
SPEC_V1 = {
    "overview": "Branch management v1",
    "api_endpoints": ["GET /branches", "POST /branches"],
    "data_entities": ["Branch"],
    "out_of_scope": ["Mobile app"],
}
SPEC_V2 = {
    "overview": "Branch management v2 — adds delete",
    "api_endpoints": ["GET /branches", "POST /branches", "DELETE /branches/{id}"],
    "data_entities": ["Branch", "AuditLog"],
    "out_of_scope": ["Mobile app"],
}


def _check(name: str, passed: bool, detail: str = "") -> None:
    icon = "PASS" if passed else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"  [{icon}] {name}{suffix}")
    CHECKS.append((name, passed, detail))


def _load_skill(name: str):
    """Load a skill by file path (v1.0 is not a valid dotted import)."""
    path = Path(__file__).resolve().parent.parent / "skills" / name / "v1.0" / "skill.py"
    spec = spec_from_file_location(name, path)
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return getattr(mod, name)


async def main() -> int:
    print("M11 Spec Registry verification")
    print("=" * 50)

    spec_registry._reset_registry()
    query_spec_registry = _load_skill("query_spec_registry")
    write_spec_registry = _load_skill("write_spec_registry")

    comp = component_slug("Build a Branch Management API")

    # ── Check 1: Run 1 creates version 1 ─────────────────────────────────────
    r1 = await write_spec_registry(
        component=comp, spec_body=SPEC_V1, run_id="run-1", prior_spec=None,
    )
    chain1 = await query_spec_registry(component=comp)
    _check(
        "Run 1 creates a Spec node (version 1) in the Registry",
        r1["version"] == 1 and r1["superseded"] is None and chain1["found"]
        and chain1["current"]["run_id"] == "run-1",
        f"slug={comp}, version={r1['version']}, versions={len(chain1['history'])}",
    )

    # ── Check 2: Run 2 retrieves Run 1's spec before drafting ────────────────
    seen_by_run2 = await query_spec_registry(component=comp)
    prior = seen_by_run2.get("current")
    _check(
        "Run 2 retrieves Run 1's spec before drafting (cross-run persistence)",
        seen_by_run2["found"] and prior is not None and prior["version"] == 1
        and prior["run_id"] == "run-1" and prior["body"]["overview"] == SPEC_V1["overview"],
        f"prior_version={prior['version'] if prior else None}",
    )

    # ── Check 3: Run 2 supersedes Run 1 with a recorded reason ───────────────
    r2 = await write_spec_registry(
        component=comp, spec_body=SPEC_V2, run_id="run-2", prior_spec=prior,
        supersedes_reason="scope expanded: added delete + audit",
    )
    chain2 = await query_spec_registry(component=comp)
    hist = chain2["history"]
    v1 = next((s for s in hist if s["version"] == 1), {})
    v2 = next((s for s in hist if s["version"] == 2), {})
    _check(
        "Run 2 supersedes Run 1 → SUPERSEDES link + reason + chain [v1→v2]",
        r2["version"] == 2
        and r2["superseded"] is not None
        and r2["superseded"]["reason"] == "scope expanded: added delete + audit"
        and v1.get("status") == "superseded"
        and v1.get("superseded_reason") == "scope expanded: added delete + audit"
        and v2.get("status") == "current"
        and chain2["current"]["version"] == 2,
        f"versions={[s['version'] for s in hist]}, current_v={chain2['current']['version']}",
    )

    # ── Check 4: scope-guard checks Registry conformance ─────────────────────
    # Drift WITHOUT a recorded supersession → red.
    st_drift = ContinuumState(request="Build a Branch Management API")
    st_drift.component = comp
    st_drift.story = {"spec": SPEC_V2}
    st_drift.registry_current_before = {"id": "spec-old", "body": SPEC_V1}
    st_drift.spec_superseded = None
    ok_drift, detail_drift = await gate_scope_conformance(st_drift)

    # Same drift WITH a recorded supersession → green.
    st_ok = ContinuumState(request="Build a Branch Management API")
    st_ok.component = comp
    st_ok.story = {"spec": SPEC_V2}
    st_ok.registry_current_before = {"id": "spec-old", "body": SPEC_V1}
    st_ok.spec_superseded = {"old_id": "spec-old", "reason": "scope expanded"}
    ok_super, _ = await gate_scope_conformance(st_ok)

    _check(
        "scope-guard: undeclared drift → red, superseded drift → green",
        (not ok_drift) and ("drift" in detail_drift) and ok_super,
        f"drift_red={not ok_drift}, superseded_green={ok_super}",
    )

    # ── Summary ──────────────────────────────────────────────────────────────
    print()
    passed_n = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    print(f"{passed_n}/{total} checks passed")
    return 0 if passed_n == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
