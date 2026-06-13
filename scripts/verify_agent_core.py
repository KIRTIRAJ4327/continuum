"""
Standalone verification for the agent execution core (offline path).

Runs each M0 agent in sequence against a single ContinuumState and asserts the
expected artifact lands on state. Does NOT require langgraph / a live model /
Neo4j — it exercises `agent_runner.run_agent` directly via the deterministic
offline path (no Azure credentials present).

Usage:  python scripts/verify_agent_core.py
"""
import asyncio
import sys
from pathlib import Path

# Make the repo root importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.agent_runner import AgentContext, run_agent  # noqa: E402
from orchestrator.state import ContinuumState  # noqa: E402


async def main() -> int:
    state = ContinuumState(request="Build a product dashboard with recent activity")
    ctx = AgentContext(repo_path=".")

    checks = []

    await run_agent(state, "bsa", ctx)
    checks.append(("BSA produced a story", bool(state.story and state.story.get("title"))))
    checks.append(("BSA story has spec", bool(state.story and state.story.get("spec"))))
    checks.append(("current_agent == bsa", getattr(state.current_agent, "value", None) == "bsa"))

    await run_agent(state, "architect", ctx)
    checks.append(("Architect produced a contract", bool(state.contract)))
    checks.append(("Architect produced a schema", bool(state.schema)))
    checks.append(("Architect produced a DAG", bool(state.dag and state.dag.get("tasks"))))

    await run_agent(state, "planner", ctx)
    checks.append(("Planner produced a plan", bool(state.dag and state.dag.get("plan"))))

    await run_agent(state, "developer", ctx)
    checks.append(("Developer produced code", bool(state.code)))

    await run_agent(state, "security", ctx)
    sec_gate = next((g for g in state.gates if g.name == "security_sast"), None)
    checks.append(("Security recorded a gate", sec_gate is not None))

    # An unknown / spec-less agent should no-op gracefully (not crash).
    await run_agent(state, "code_review", ctx)
    checks.append(("Spec-less agent did not crash", True))

    checks.append(("Message recorded per agent", len(state.messages) >= 5))

    print("\nAgent core verification")
    print("=" * 40)
    ok = True
    for label, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok = ok and passed

    print("=" * 40)
    print(f"Artifacts: story={bool(state.story)} contract={bool(state.contract)} "
          f"schema={bool(state.schema)} dag_tasks={len(state.dag.get('tasks', [])) if state.dag else 0} "
          f"code_files={len(state.code or {})} gates={[g.name for g in state.gates]}")
    print("RESULT:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
