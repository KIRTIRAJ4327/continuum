"""
Tests for the agent execution core (`orchestrator.agent_runner`).

These exercise the deterministic offline path, so they run without langgraph,
a live model, or Neo4j. `asyncio_mode = "auto"` (pyproject) handles the async
test functions.
"""
import pytest

from orchestrator.agent_runner import (
    AgentContext,
    apply_agent_output,
    load_agent_spec,
    load_skill,
    run_agent,
)
from orchestrator.state import AgentRole, ContinuumState


@pytest.fixture
def ctx() -> AgentContext:
    return AgentContext(repo_path=".")


@pytest.fixture
def state() -> ContinuumState:
    return ContinuumState(request="Build a product dashboard with recent activity")


async def test_bsa_produces_story(state, ctx):
    await run_agent(state, "bsa", ctx)
    assert state.current_agent == AgentRole.BSA
    assert state.story and state.story.get("title")
    assert state.story.get("spec")  # write_spec folded into the story
    assert len(state.messages) == 1


async def test_architect_produces_contract_schema_dag(state, ctx):
    await run_agent(state, "bsa", ctx)
    await run_agent(state, "architect", ctx)
    assert state.contract
    assert state.schema
    assert state.dag and state.dag.get("tasks")
    assert state.dag.get("components")


async def test_full_offline_pipeline(state, ctx):
    for role in ("bsa", "architect", "planner", "developer", "security"):
        await run_agent(state, role, ctx)

    assert state.story
    assert state.contract and state.schema
    assert state.dag.get("tasks") and state.dag.get("plan")
    assert state.code  # developer wrote at least one file
    sec_gate = next((g for g in state.gates if g.name == "security_sast"), None)
    assert sec_gate is not None and sec_gate.status in ("green", "red")
    assert len(state.messages) == 5


async def test_specless_agent_noops(state, ctx):
    """An M1+ agent with no YAML spec should no-op, not crash."""
    await run_agent(state, "code_review", ctx)
    assert state.current_agent == AgentRole.CODE_REVIEW
    assert state.story is None  # nothing produced


def test_load_agent_spec_known_role():
    spec = load_agent_spec("bsa")
    assert spec["name"] == "BSA"
    assert "create_story" in spec["allowed_skills"]


def test_load_skill_present_and_missing():
    assert callable(load_skill("create_story"))
    assert load_skill("definitely_not_a_skill") is None


def test_apply_developer_output_list_form():
    """Developer output may be a list of {file_path, content}; normalise to dict."""
    state = ContinuumState(request="x")
    apply_agent_output(
        state,
        AgentRole.DEVELOPER.value,
        {
            "code": [{"file_path": "src/app.py", "content": "print(1)"}],
            "tests": [{"file_path": "tests/test_app.py", "content": "assert True"}],
        },
    )
    assert state.code == {
        "src/app.py": "print(1)",
        "tests/test_app.py": "assert True",
    }


def test_apply_security_failure_sets_red_gate():
    state = ContinuumState(request="x")
    apply_agent_output(
        state,
        AgentRole.SECURITY.value,
        {"pass": False, "issues": [{"type": "xss", "severity": "high"}]},
    )
    gate = next(g for g in state.gates if g.name == "security_sast")
    assert gate.status == "red" and gate.error_message
