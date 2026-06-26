#!/usr/bin/env python
"""
verify_c2_observability.py — C2 Observability cockpit (3/3).

Verifies the richer event vocabulary surfaces on the OFFLINE pipeline, so the
cockpit (TraceTimeline / ActivityStream / RunMetrics) is meaningful in demo mode
without Azure credentials.

  A. An offline run emits the C2 events: sensor_result, artifact_ready,
     agent_milestone, evidence_built.
  B. Every event carries a monotonic per-run seq (0..n, no gaps) — the C1
     invariant the SSE id/Last-Event-ID resume relies on.
  C. sensor_result events carry {sensor, status, detail} and evidence_built
     carries the 6-layer stack.

Pure + offline: drives api.main._execute_pipeline with a run_id against the
in-memory event bus. No network, no Azure.

Usage:
  python scripts/verify_c2_observability.py
Expected output:
  3/3 checks passed
"""
from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.events import event_bus  # noqa: E402
from orchestrator.state import ContinuumState  # noqa: E402

CHECKS: list = []


def _check(name: str, passed: bool, detail: str = "") -> None:
    status = "PASS" if passed else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"  [{status}] {name}{suffix}")
    CHECKS.append((name, passed))


async def main() -> int:
    print()
    print("C2 Observability verification (3/3)")
    print("=" * 40)

    from api.main import _execute_pipeline

    rid = "c2-verify"
    event_bus.purge(rid)
    state = ContinuumState(run_id=rid, request="Add a status field to orders", business_mappings=[])
    await _execute_pipeline(state, rid)
    history = event_bus.run_history(rid)
    types = {e.get("event_type") for e in history}

    # ── Case A: C2 event types present ───────────────────────────────────── #
    print("\nCase A: C2 events emitted on the offline path")
    want = {"sensor_result", "artifact_ready", "agent_milestone", "evidence_built"}
    missing = want - types
    _check("sensor_result/artifact_ready/agent_milestone/evidence_built all emitted",
           not missing, f"missing={sorted(missing) or 'none'}")

    # ── Case B: monotonic seq 0..n ───────────────────────────────────────── #
    print("\nCase B: monotonic per-run seq (no gaps)")
    seqs = [e.get("seq") for e in history]
    _check("seq is 0..n with no gaps", seqs == list(range(len(seqs))),
           f"n={len(seqs)} ok={seqs == list(range(len(seqs)))}")

    # ── Case C: payload shapes ───────────────────────────────────────────── #
    print("\nCase C: event payload shapes")
    sensors = [e for e in history if e.get("event_type") == "sensor_result"]
    sensor_ok = bool(sensors) and all(
        {"sensor", "status", "detail"} <= set(e.get("data", {})) for e in sensors
    )
    ev_built = next((e for e in history if e.get("event_type") == "evidence_built"), None)
    layers = (ev_built or {}).get("data", {}).get("layers", []) if ev_built else []
    _check(
        "sensor_result shape + evidence_built has 6 layers",
        sensor_ok and len(layers) == 6,
        f"sensors={len(sensors)} layers={len(layers)}",
    )

    # ── Summary ──────────────────────────────────────────────────────────── #
    print()
    print("=" * 40)
    passed = sum(1 for _, ok in CHECKS if ok)
    total = len(CHECKS)
    print(f"{passed}/{total} checks passed")
    print()
    if passed == total:
        print("C2 Observability: OK")
    else:
        print("FAIL — fix the checks above")
        for name, ok in CHECKS:
            if not ok:
                print(f"  FAILED: {name}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
