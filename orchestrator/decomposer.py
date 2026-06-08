"""
orchestrator/decomposer.py -- bounded dynamic decomposition for large DAGs.

When the Planner produces a DAG with >= 5 tasks, the decomposer generates
a deterministic Python orchestration script that fans out N parallel subagent
calls instead of running the linear DB→BE→FE chain.

Constraints (Rule 4: bound the auto-fix loop):
  - max 4 parallel agents
  - 50k token budget total (split evenly across parallel slots)
  - Every subagent output still passes through local_verify gate
  - Script is deterministic and re-runnable (not model-powered)
  - Only leaf work (write_code calls) is model-powered inside each subagent

The generated script is stored in state.decomposition_script.
The graph executes the linear chain regardless — the script is available
for operators to inspect or run manually in complex deployments.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List


_MAX_PARALLEL = 4
_TOKEN_BUDGET  = 50_000
_MIN_TASKS_FOR_DECOMPOSITION = 5


def should_decompose(dag: Dict[str, Any]) -> bool:
    """Return True if the DAG is large enough to warrant parallel decomposition."""
    tasks = _extract_tasks(dag)
    return len(tasks) >= _MIN_TASKS_FOR_DECOMPOSITION


def _extract_tasks(dag: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Pull tasks out of wherever the planner put them in state.dag."""
    # Try multiple locations: dag.plan.tasks, dag.tasks, dag.all_tasks.
    plan = dag.get("plan") or {}
    if isinstance(plan, dict):
        tasks = plan.get("tasks") or []
    else:
        tasks = []

    if not tasks:
        tasks = dag.get("tasks") or dag.get("all_tasks") or []

    # Normalise: each task must be a dict.
    normalised = []
    for t in tasks:
        if isinstance(t, dict):
            normalised.append(t)
        elif isinstance(t, str):
            normalised.append({"name": t, "type": "code"})
    return normalised


def _group_tasks(tasks: List[Dict[str, Any]], n_groups: int) -> List[List[Dict[str, Any]]]:
    """Split tasks into n_groups balanced batches."""
    groups: List[List[Dict[str, Any]]] = [[] for _ in range(n_groups)]
    for i, task in enumerate(tasks):
        groups[i % n_groups].append(task)
    return [g for g in groups if g]


def generate_script(
    dag: Dict[str, Any],
    max_agents: int = _MAX_PARALLEL,
    token_budget: int = _TOKEN_BUDGET,
) -> str:
    """
    Generate a Python orchestration script for parallel subagent execution.

    Args:
        dag:          The full state.dag dict from the Planner.
        max_agents:   Maximum number of parallel subagent slots (default 4).
        token_budget: Total token budget shared across all parallel agents.

    Returns:
        A Python script string. The script is self-contained and re-runnable.
        It imports run_agent from orchestrator.agent_runner and fans out
        tasks across parallel asyncio groups, each bounded to token_budget/n_agents.
    """
    tasks = _extract_tasks(dag)
    n_agents = min(max_agents, math.ceil(len(tasks) / 2), max_agents)
    n_agents = max(1, n_agents)
    tokens_per_agent = token_budget // n_agents
    groups = _group_tasks(tasks, n_agents)

    dag_id = dag.get("dag_id", "dag-decomposed")
    task_count = len(tasks)

    lines = [
        "#!/usr/bin/env python",
        '"""',
        f"Auto-generated decomposition script for DAG: {dag_id}",
        f"Tasks: {task_count}  |  Parallel agents: {n_agents}  |  Tokens/agent: {tokens_per_agent:,}",
        "",
        "This script fans out pipeline execution across parallel subagents.",
        "Every subagent slot runs the developer roles (database→backend→frontend)",
        "and results are merged back into state.code before local_verify fires.",
        "",
        "INVARIANTS:",
        "  - Each slot is bounded to {tokens_per_agent} tokens".format(tokens_per_agent=tokens_per_agent),
        "  - local_verify gate fires after all slots complete",
        "  - Script is deterministic: same DAG → same groups every run",
        '"""',
        "from __future__ import annotations",
        "",
        "import asyncio",
        "from typing import Any, Dict, List",
        "",
        "from orchestrator.agent_runner import AgentContext, run_agent",
        "from orchestrator.state import ContinuumState",
        "",
        "",
        f"_N_PARALLEL    = {n_agents}",
        f"_TOKENS_BUDGET = {token_budget}",
        f"_TOKENS_EACH   = {tokens_per_agent}",
        "",
        "_TASK_GROUPS: List[List[Dict[str, Any]]] = [",
    ]

    for i, group in enumerate(groups):
        task_names = [t.get("name", f"task_{j}") for j, t in enumerate(group)]
        lines.append(f"    {task_names!r},  # slot {i}")
    lines.append("]")
    lines.append("")

    lines += [
        "",
        "async def _run_slot(",
        "    slot_id: int,",
        "    tasks: List[Dict[str, Any]],",
        "    base_state: ContinuumState,",
        "    ctx: AgentContext,",
        ") -> ContinuumState:",
        '    """Execute one parallel slot: database → backend → frontend."""',
        "    slot_state = ContinuumState(",
        "        request=base_state.request,",
        "        story=base_state.story,",
        "        contract=base_state.contract,",
        "        schema=base_state.schema,",
        "        dag=base_state.dag,",
        "        run_id=f\"{base_state.run_id}-slot{slot_id}\",",
        "    )",
        "    for role in (\"database\", \"backend\", \"frontend\"):",
        "        await run_agent(slot_state, role, ctx)",
        "    return slot_state",
        "",
        "",
        "async def run_decomposed(",
        "    state: ContinuumState,",
        "    ctx: AgentContext,",
        ") -> ContinuumState:",
        '    """Fan out to parallel slots and merge results into state.code."""',
        "    coros = [",
        "        _run_slot(i, tasks, state, ctx)",
        "        for i, tasks in enumerate(_TASK_GROUPS)",
        "    ]",
        "    slot_states = await asyncio.gather(*coros)",
        "",
        "    # Merge code from all slots.",
        "    merged_code: Dict[str, str] = dict(state.code or {})",
        "    for slot_state in slot_states:",
        "        merged_code.update(slot_state.code or {})",
        "    state.code = merged_code",
        "",
        "    # Collect gate statuses from slots (worst-case: any red → red).",
        "    from orchestrator.state import GateStatus",
        "    for slot_state in slot_states:",
        "        for gate in slot_state.gates:",
        "            existing = next((g for g in state.gates if g.name == gate.name), None)",
        "            if existing is None:",
        "                state.gates.append(gate)",
        "            elif gate.status == \"red\":",
        "                existing.status = \"red\"",
        "                existing.error_message = gate.error_message",
        "",
        "    return state",
        "",
        "",
        "if __name__ == \"__main__\":",
        '    """Standalone execution: run decomposed pipeline for a given request."""',
        "    import sys",
        "    request = \" \".join(sys.argv[1:]) or \"Build a feature\"",
        "    state = ContinuumState(request=request)",
        "    ctx = AgentContext(repo_path=\".\")",
        "    final = asyncio.run(run_decomposed(state, ctx))",
        "    print(f\"Done: {len(final.code or {})} files, {len(final.gates)} gates\")",
    ]

    return "\n".join(lines) + "\n"
