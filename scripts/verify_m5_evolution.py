"""
verify_m5_evolution.py -- prove the Evolution Agent + decomposer work end-to-end.

Runs 6 checks:
  1. Run pipeline -> events generated (Memory agent writes an episode).
  2. evolution.agent.observe() -> failure patterns found (offline stubs at minimum).
  3. evolution.agent.propose() -> proposal generated with required fields.
  4. evolution.evaluator.evaluate(proposal) -> eval runs, returns {safe, improved, delta_pp}.
  5. Proposal written to evolution/proposals/pending/.
  6. promoter.human_promote(proposal_id) -> verify-offline passes, proposal applied.

Usage:
    python scripts/verify_m5_evolution.py
"""
from __future__ import annotations

import asyncio
import io
import json
import sys
from pathlib import Path

# Force UTF-8 output on Windows.
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graph_db.driver import Neo4jDriver             # noqa: E402
from orchestrator.agent_runner import AgentContext, run_agent  # noqa: E402
from orchestrator.state import ContinuumState        # noqa: E402

_REQUEST = "Build a multi-tenant analytics dashboard with real-time metrics"

_CHECKS: list[tuple[str, bool]] = []


def _check(name: str, passed: bool, detail: str = "") -> None:
    status = "PASS" if passed else "FAIL"
    suffix = f"  [{detail}]" if detail else ""
    print(f"  [{status}] {name}{suffix}")
    _CHECKS.append((name, passed))


async def _run_pipeline() -> ContinuumState:
    driver = Neo4jDriver("bolt://localhost:7687", "neo4j", "continuum-dev")
    ctx = AgentContext(repo_path=".", neo4j_driver=driver)
    state = ContinuumState(request=_REQUEST, run_id="verify-m5-01")
    for role in ("bsa", "architect", "planner", "developer", "security", "memory"):
        await run_agent(state, role, ctx)
    return state


async def main() -> int:
    print()
    print("M5 Evolution Agent verification (6/6)")
    print("=" * 60)

    # ------------------------------------------------------------------
    # Check 1: Run pipeline → events generated
    # ------------------------------------------------------------------
    print("\nCheck 1: Run pipeline and generate event history...")
    try:
        state = await _run_pipeline()
        from orchestrator.events import event_bus
        all_events = dict(event_bus._log)
        total_events = sum(len(v) for v in all_events.values())
        _check(
            "Pipeline run produces events",
            total_events > 0 or (state.episodes_written or []),
            f"{total_events} events, {len(state.episodes_written or [])} episode(s) written",
        )
    except Exception as exc:
        _check("Pipeline run produces events", False, str(exc))
        state = ContinuumState(request=_REQUEST)

    # ------------------------------------------------------------------
    # Check 2: observe() finds failure patterns
    # ------------------------------------------------------------------
    print("\nCheck 2: Evolution agent observes failure patterns...")
    try:
        from evolution.agent import EvolutionAgent
        evo = EvolutionAgent()
        patterns = evo.observe()
        _check(
            "observe() returns at least 1 pattern",
            len(patterns) >= 1,
            f"{len(patterns)} pattern(s): {[p.get('failure_type') for p in patterns[:3]]}",
        )
    except Exception as exc:
        _check("observe() returns at least 1 pattern", False, str(exc))
        patterns = []

    # ------------------------------------------------------------------
    # Check 3: propose() generates a valid proposal
    # ------------------------------------------------------------------
    print("\nCheck 3: Evolution agent proposes a concrete change...")
    proposal: dict = {}
    try:
        from evolution.agent import EvolutionAgent
        evo = EvolutionAgent()
        diagnoses = evo.diagnose(patterns or evo.observe())
        proposal = evo.propose(diagnoses)
        required_keys = {"id", "type", "file", "before", "after", "rationale"}
        has_keys = required_keys.issubset(proposal.keys())
        _check(
            "propose() returns proposal with required fields",
            bool(proposal) and has_keys,
            f"id={proposal.get('id')}, type={proposal.get('type')}, file={proposal.get('file')}",
        )
    except Exception as exc:
        _check("propose() returns proposal with required fields", False, str(exc))

    # ------------------------------------------------------------------
    # Check 4: evaluator.evaluate(proposal) runs and returns {safe, improved}
    # ------------------------------------------------------------------
    print("\nCheck 4: Evaluator scores the proposal...")
    eval_result: dict = {}
    try:
        from evolution import evaluator
        eval_result = await evaluator.evaluate(proposal, fast=True)
        has_shape = all(k in eval_result for k in ("improved", "delta_pp", "safe"))
        _check(
            "evaluate() returns {improved, delta_pp, safe}",
            has_shape,
            f"improved={eval_result.get('improved')}, delta_pp={eval_result.get('delta_pp'):.4f}, "
            f"safe={eval_result.get('safe')}, method={eval_result.get('method')}",
        )
    except Exception as exc:
        _check("evaluate() returns {improved, delta_pp, safe}", False, str(exc))

    # ------------------------------------------------------------------
    # Check 5: Proposal written to evolution/proposals/pending/
    # ------------------------------------------------------------------
    print("\nCheck 5: Proposal written to pending/ directory...")
    try:
        root = Path(__file__).resolve().parent.parent
        pending_dir = root / "evolution" / "proposals" / "pending"
        pid = proposal.get("id", "")
        pending_file = pending_dir / f"{pid}.json" if pid else None

        if pending_file and pending_file.exists():
            saved = json.loads(pending_file.read_text(encoding="utf-8"))
            _check(
                "Proposal JSON written to pending/",
                saved.get("id") == pid,
                str(pending_file.relative_to(root)),
            )
        else:
            _check("Proposal JSON written to pending/", False, f"file not found: {pending_file}")
    except Exception as exc:
        _check("Proposal JSON written to pending/", False, str(exc))

    # ------------------------------------------------------------------
    # Check 6: human_promote(proposal_id) applies change and verify-offline passes
    # ------------------------------------------------------------------
    print("\nCheck 6: human_promote() applies change and verify-offline passes...")
    try:
        # Create a safe proposal targeting a stub file so we don't modify real harness files.
        root = Path(__file__).resolve().parent.parent
        stub_path = root / "evolution" / "proposals" / "_test_stub.txt"
        stub_path.write_text("before: placeholder content\n", encoding="utf-8")

        from evolution.agent import _write_pending
        import uuid
        import time
        safe_proposal = {
            "id": f"evo-test-{uuid.uuid4().hex[:6]}",
            "type": "prompt_edit",
            "file": str(stub_path.relative_to(root)).replace("\\", "/"),
            "before": "before: placeholder content",
            "after": "after: updated by evolution agent",
            "rationale": "Verify script test — safe stub file only",
            "diagnosis_type": "prompt_issue",
            "confidence": 0.9,
            "pattern": {},
            "recommendation": "test",
            "estimated_improvement_pp": 5.0,
            "created_at": int(time.time()),
            "status": "pending",
        }
        _write_pending(safe_proposal)

        from evolution.promoter import human_promote
        result = human_promote(safe_proposal["id"])

        _check(
            "human_promote() succeeds and verify-offline passes",
            result.get("success", False),
            f"message={result.get('message', '')[:80]}",
        )

        # Clean up the stub file.
        if stub_path.exists():
            stub_path.unlink()

    except Exception as exc:
        _check("human_promote() succeeds and verify-offline passes", False, str(exc))

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print()
    print("=" * 60)
    passed = sum(1 for _, ok in _CHECKS if ok)
    total = len(_CHECKS)
    print(f"Result: {passed}/{total} PASS")
    print()

    if passed == total:
        print("M5 Evolution Agent: OK")
    else:
        print("FAIL - fix the checks above")
        for name, ok in _CHECKS:
            if not ok:
                print(f"  FAILED: {name}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
