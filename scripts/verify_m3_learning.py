"""
verify_m3_learning.py -- demonstrate that run N learns from run N-1.

Runs the pipeline TWICE against the same feature request using a shared
in-memory Neo4jDriver (no actual Neo4j connection required).

Expected proof of learning:
  Run 1:  Memory agent writes an Episode.  state1.episodes_written is non-empty.
  Run 2:  BSA calls graphrag_query, which finds the Episode from run 1.
           state2.episodes is non-empty and references run-1 content.

Usage:
    python scripts/verify_m3_learning.py
"""
from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

# Force UTF-8 output on Windows so the check/cross symbols survive.
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Make the repo root importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graph_db.driver import Neo4jDriver  # noqa: E402
from orchestrator.agent_runner import AgentContext, run_agent  # noqa: E402
from orchestrator.state import ContinuumState  # noqa: E402

_REQUEST = "Build a product dashboard with recent activity feed"

# Pipeline roles in order (matches _execute_pipeline in api/main.py).
_ROLES = ["bsa", "architect", "planner", "developer", "security", "memory"]


async def _run_pipeline(request: str, ctx: AgentContext) -> ContinuumState:
    """Run the full offline pipeline and return the final state."""
    state = ContinuumState(request=request)
    for role in _ROLES:
        await run_agent(state, role, ctx)
        if state.human_approval_pending:
            break
    return state


async def main() -> int:
    """Execute two runs and check that run 2 uses run 1's memory."""

    # One Neo4jDriver shared across both runs -- its _in_memory_episodes list
    # is the offline episode store.  We do NOT call driver.connect() so it
    # stays in offline mode throughout.
    driver = Neo4jDriver(
        uri="bolt://localhost:7687",
        user="neo4j",
        password="continuum-dev",
    )

    ctx = AgentContext(repo_path=".", neo4j_driver=driver)

    checks: list[tuple[str, bool]] = []

    # -- Run 1 -----------------------------------------------------------------
    print("\nRun 1 -- executing pipeline (offline)...")
    state1 = await _run_pipeline(_REQUEST, ctx)

    wrote_ep = bool(state1.episodes_written)
    checks.append(("Run 1: Memory agent wrote >=1 episode", wrote_ep))

    im_count = len(driver._in_memory_episodes)
    checks.append(("Run 1: in-memory episode store is non-empty", im_count > 0))

    if im_count > 0:
        first_ep = driver._in_memory_episodes[0]
        has_request = _REQUEST.split()[0].lower() in first_ep.get("request_text", "").lower()
        checks.append(("Run 1 episode contains original request text", has_request))

    print(f"  Episodes in store after run 1: {im_count}")
    for ep in driver._in_memory_episodes:
        outcome_str = str(ep.get("outcome", ""))[:57]
        print(f"  -> agent={ep.get('agent')} outcome={outcome_str!r}")

    # -- Run 2 -----------------------------------------------------------------
    print("\nRun 2 -- executing pipeline with memory grounding...")
    state2 = await _run_pipeline(_REQUEST, ctx)

    retrieved = state2.episodes or []
    checks.append(("Run 2: BSA retrieved past episodes", len(retrieved) > 0))

    if retrieved:
        found_text = any(
            _REQUEST.split()[0].lower() in str(
                ep.get("request_text", "") or ep.get("decision", "")
            ).lower()
            for ep in retrieved
        )
        checks.append(("Run 2 episodes reference run-1 content", found_text))
        print(f"  Episodes retrieved by BSA in run 2: {len(retrieved)}")
        for ep in retrieved:
            score = str(ep.get("score", "?"))[:6]
            decision_str = str(ep.get("decision", ""))[:57]
            print(f"  -> score={score} agent={ep.get('agent')!r} decision={decision_str!r}")
    else:
        checks.append(("Run 2 episodes reference run-1 content", False))
        print("  [!] No past episodes retrieved -- grounding did not fire")

    im_count2 = len(driver._in_memory_episodes)
    checks.append(("Episode store grows across runs", im_count2 > im_count))
    print(f"  Episodes in store after run 2: {im_count2}")

    # -- Report ----------------------------------------------------------------
    print("\nM3 learning verification")
    print("=" * 60)
    passed = 0
    for label, ok in checks:
        icon = "PASS" if ok else "FAIL"
        print(f"  [{icon}] {label}")
        if ok:
            passed += 1
    total = len(checks)
    print("=" * 60)
    print(f"Artifacts: story={bool(state2.story)} episodes_written={bool(state2.episodes_written)}")
    if passed == total:
        print(f"RESULT: ALL PASS ({passed}/{total})")
    else:
        print(f"RESULT: {passed}/{total} PASS -- {total - passed} FAIL")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
