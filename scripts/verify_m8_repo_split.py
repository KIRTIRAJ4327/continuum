#!/usr/bin/env python
"""
verify_m8_repo_split.py — M8 Two-Layer Repo Split (D33) offline verification.

Three checks:
  1. samples/target-app/ Layer 2 placeholder exists with .pdlc/ directory.
  2. emit_pdlc_artifacts() writes correct .pdlc/ structure (manifest + evidence
     + contracts/ + generated/) to a temporary target repo.
  3. .pdlc/evidence.json contains all 6 Evidence Stack layers with required keys.

Usage:
  python scripts/verify_m8_repo_split.py
Expected output:
  3/3 checks passed
"""
from __future__ import annotations

import asyncio
import io
import json
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from evals.evidence_stack import EVIDENCE_LAYER_COUNT
from orchestrator.pdlc import emit_pdlc_artifacts
from orchestrator.state import ContinuumState

CHECKS: list = []


def _check(name: str, passed: bool, detail: str = "") -> None:
    icon = "PASS" if passed else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"  [{icon}] {name}{suffix}")
    CHECKS.append((name, passed, detail))


async def main() -> int:
    print("M8 Two-Layer Repo Split verification")
    print("=" * 50)

    # ── Check 1: Layer 2 placeholder exists ──────────────────────────────────
    target_app = _REPO / "samples" / "target-app"
    pdlc_dir = target_app / ".pdlc"
    _check(
        "Layer 2 placeholder: samples/target-app/.pdlc/ exists",
        pdlc_dir.is_dir() and (pdlc_dir / "README.md").exists(),
        f"path={pdlc_dir}",
    )

    # ── Check 2: emit_pdlc_artifacts creates correct .pdlc/ structure ────────
    with tempfile.TemporaryDirectory() as tmp:
        state = ContinuumState(request="add product listing with pagination")
        state.run_id = "m8-test-01"
        state.run_status = "done"
        state.cost_usd = 0.08
        state.code = {
            "src/main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
            "src/models.py": "from sqlalchemy.orm import DeclarativeBase\nclass Base(DeclarativeBase): pass\n",
        }
        state.contract = (
            "openapi: '3.0.0'\n"
            "info:\n  title: Product API\n  version: '1.0'\n"
            "paths:\n  /products:\n    get:\n      responses:\n        '200':\n          description: OK\n"
        )
        state.schema = "CREATE TABLE products (id UUID PRIMARY KEY, name TEXT NOT NULL);"

        result = await emit_pdlc_artifacts(state, tmp)
        pdlc = Path(tmp) / ".pdlc"

        manifest_ok = (pdlc / "run_manifest.json").exists()
        evidence_ok = (pdlc / "evidence.json").exists()
        contracts_ok = (pdlc / "contracts" / "openapi.yaml").exists()
        schema_ok = (pdlc / "contracts" / "schema.sql").exists()
        generated_ok = (pdlc / "generated" / "src" / "main.py").exists()

        all_ok = manifest_ok and evidence_ok and contracts_ok and schema_ok and generated_ok
        detail = (
            f"files_written={result['files_written']} "
            f"manifest={manifest_ok} evidence={evidence_ok} "
            f"contracts={contracts_ok} generated={generated_ok}"
        )
        _check("emit_pdlc_artifacts writes .pdlc/ structure", all_ok, detail)

        # ── Check 3: evidence.json has all 6 Evidence Stack layers ───────────
        evidence_data = json.loads((pdlc / "evidence.json").read_text(encoding="utf-8"))
        layers_ok = (
            len(evidence_data) == EVIDENCE_LAYER_COUNT
            and all(
                "layer" in e and "status" in e and "detail" in e
                for e in evidence_data
            )
        )
        _check(
            f"evidence.json has {EVIDENCE_LAYER_COUNT} layers with required keys",
            layers_ok,
            f"layers={len(evidence_data)} required={EVIDENCE_LAYER_COUNT}",
        )

    # ── Summary ──────────────────────────────────────────────────────────────
    print()
    passed_n = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    print(f"{passed_n}/{total} checks passed")
    return 0 if passed_n == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
