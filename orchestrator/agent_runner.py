# File: continuum/orchestrator/agent_runner.py
# Agent execution core — implements the per-agent run pipeline used by the
# orchestrator's agent nodes (graph.py `_run_agent`).

"""
Agent execution engine for Continuum.

When the orchestrator routes to an agent node, this module:
  1. Loads the agent's YAML spec (`agents/<role>.yaml`).
  2. Resolves the model for the agent's `model_tier` ("strong"/"cheap").
  3. Loads the agent's `allowed_skills` (versioned modules under `skills/`).
  4. Runs the agent:
       - LLM path: build a prompt from the spec + current state, bind the
         available skills as tools, run a bounded tool-calling loop, and parse
         the final JSON answer.
       - Offline path (no model configured): call the agent's primary skills
         directly to produce well-formed artifacts. This keeps the M0 skeleton
         runnable and testable without live Azure credentials.
  5. Maps the produced artifacts onto `ContinuumState`.

Design constraints honoured here (PRD §4):
  - Skills are atoms: versioned, named functions loaded from `skills/<name>/v*`.
  - The agent only touches state through its declared skills + structured output.
  - Heavy LLM dependencies are imported lazily so this module is usable (and
    testable offline) without langgraph/langchain installed.
"""
from __future__ import annotations

import importlib.util
import inspect
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from .state import AgentMessage, AgentRole, ContinuumState, GateStatus

logger = logging.getLogger(__name__)

# Repo root = parent of the `orchestrator/` package directory.
_ROOT = Path(__file__).resolve().parent.parent
_AGENTS_DIR = _ROOT / "agents"
_SKILLS_DIR = _ROOT / "skills"

# Max LLM<->tool round-trips before we force a final answer.
MAX_TOOL_ITERS = 5

# Parameters that are injected from the run context, never requested from the LLM.
_INJECTED_PARAMS = {
    "neo4j_driver",
    "sandbox",
    "repo_path",
    "auth_token",
    "jira_token",
    "ado_token",
}


# --------------------------------------------------------------------------- #
# Run context — shared resources injected into skills
# --------------------------------------------------------------------------- #
@dataclass
class AgentContext:
    """Shared resources available to skills during an agent run."""

    neo4j_driver: Any = None
    sandbox: Any = None
    repo_path: str = "."
    tokens: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "AgentContext":
        """Build a context from environment variables (best-effort, no I/O)."""
        ado = os.getenv("AZURE_DEVOPS_TOKEN", "")
        return cls(
            repo_path=os.getenv("REPO_PATH", "."),
            tokens={"jira_token": ado, "ado_token": ado, "auth_token": ado},
        )


# --------------------------------------------------------------------------- #
# Spec + skill loading
# --------------------------------------------------------------------------- #
_spec_cache: Dict[str, dict] = {}
_skill_cache: Dict[str, Optional[Callable]] = {}


def load_agent_spec(role: str) -> dict:
    """Load and cache an agent YAML spec by role name (e.g. "bsa")."""
    if role in _spec_cache:
        return _spec_cache[role]
    path = _AGENTS_DIR / f"{role}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"No agent spec for role '{role}' at {path}")
    with open(path, "r", encoding="utf-8") as fh:
        spec = yaml.safe_load(fh) or {}
    _spec_cache[role] = spec
    return spec


def _latest_version_dir(skill_dir: Path) -> Optional[Path]:
    """Return the highest `v*` version directory inside a skill folder."""
    versions = sorted((p for p in skill_dir.glob("v*") if p.is_dir()), key=lambda p: p.name)
    return versions[-1] if versions else None


def load_skill(name: str) -> Optional[Callable]:
    """
    Load a skill function by name from `skills/<name>/v*/skill.py`.

    Skills are loaded by file path (the version dir name like "v1.0" is not a
    valid dotted module path), so the callable is resolved directly. Returns
    None — with a warning — if the skill is missing or unimplemented, so an
    agent can still run with whatever skills it does have.
    """
    if name in _skill_cache:
        return _skill_cache[name]

    fn: Optional[Callable] = None
    skill_dir = _SKILLS_DIR / name
    if skill_dir.is_dir():
        vdir = _latest_version_dir(skill_dir)
        skill_file = vdir / "skill.py" if vdir else None
        if skill_file and skill_file.exists():
            try:
                spec = importlib.util.spec_from_file_location(f"continuum_skill_{name}", skill_file)
                module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
                spec.loader.exec_module(module)  # type: ignore[union-attr]
                fn = getattr(module, name, None)
            except Exception as exc:  # noqa: BLE001 - skill load must never crash a run
                logger.warning("Failed to load skill '%s': %s", name, exc)

    if fn is None:
        logger.warning("Skill '%s' unavailable (missing or unimplemented)", name)
    _skill_cache[name] = fn
    return fn


# --------------------------------------------------------------------------- #
# Model resolution
# --------------------------------------------------------------------------- #
def _deployment_for_tier(tier: str) -> Optional[str]:
    """Map a model tier to an Azure deployment id from the environment."""
    if tier == "cheap":
        return os.getenv("AZURE_DEPLOYMENT_CHEAP") or os.getenv("AZURE_DEPLOYMENT_ID")
    return os.getenv("AZURE_DEPLOYMENT_STRONG") or os.getenv("AZURE_DEPLOYMENT_ID")


def resolve_model(spec: dict) -> Optional[Any]:
    """
    Return a LangChain chat model for the agent's tier, or None if no model is
    configured/available. None triggers the deterministic offline path.
    """
    if not (os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("AZURE_INFERENCE_CREDENTIAL")):
        logger.info("No Azure credentials in environment; using offline agent path")
        return None

    tier = spec.get("model_tier", "strong")
    deployment = _deployment_for_tier(tier)
    if not deployment:
        logger.warning("No deployment configured for tier '%s'; using offline path", tier)
        return None

    try:
        from langchain.chat_models import init_chat_model  # lazy import

        return init_chat_model(
            f"azure_ai:{deployment}",
            temperature=spec.get("temperature", 0.5),
            max_tokens=spec.get("max_tokens", 2000),
        )
    except Exception as exc:  # noqa: BLE001 - any model init failure -> offline
        logger.warning("Could not initialise model '%s': %s; using offline path", deployment, exc)
        return None


# --------------------------------------------------------------------------- #
# Prompt building
# --------------------------------------------------------------------------- #
def _state_context(state: ContinuumState) -> str:
    """Serialise the parts of state relevant as input to the next agent."""
    parts = [f"Original request: {state.request}"]
    if state.story:
        parts.append(f"Story: {json.dumps(state.story, default=str)[:1500]}")
    if state.contract:
        parts.append(f"OpenAPI contract:\n{state.contract[:1500]}")
    if state.schema:
        parts.append(f"DB schema:\n{state.schema[:1000]}")
    if state.dag:
        parts.append(f"DAG/plan: {json.dumps(state.dag, default=str)[:1000]}")
    if state.code:
        parts.append(f"Existing code files: {list(state.code.keys())}")
    return "\n".join(parts)


def build_system_prompt(spec: dict, state: ContinuumState) -> str:
    """Build the agent system prompt from its instructions + current state."""
    instructions = (spec.get("instructions") or "").strip()
    return (
        f"{instructions}\n\n"
        f"## Current pipeline state\n{_state_context(state)}\n\n"
        "Respond with ONLY the JSON object described in your instructions. "
        "No prose, no markdown code fences."
    )


# --------------------------------------------------------------------------- #
# Skill invocation (signature-aware: merges LLM args with injected context)
# --------------------------------------------------------------------------- #
def _tool_schema(name: str, fn: Callable) -> dict:
    """Build an OpenAI-style function schema for a skill, hiding injected params."""
    properties: Dict[str, Any] = {}
    required: List[str] = []
    for pname, param in inspect.signature(fn).parameters.items():
        if pname in _INJECTED_PARAMS:
            continue
        properties[pname] = {"type": "string", "description": pname}
        if param.default is inspect.Parameter.empty:
            required.append(pname)
    doc = (fn.__doc__ or name).strip().splitlines()[0]
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": doc,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


async def invoke_skill(
    fn: Callable,
    provided: Dict[str, Any],
    ctx: AgentContext,
    state: ContinuumState,
) -> Any:
    """
    Call a skill, filling each parameter from (in priority order): the run
    context (injected resources/tokens), explicitly provided args, then a few
    well-known state fields. Awaits the result if the skill is async.
    """
    call_kwargs: Dict[str, Any] = {}
    for pname in inspect.signature(fn).parameters:
        if pname == "neo4j_driver":
            call_kwargs[pname] = ctx.neo4j_driver
        elif pname == "sandbox":
            call_kwargs[pname] = ctx.sandbox
        elif pname == "repo_path":
            call_kwargs[pname] = ctx.repo_path
        elif pname in _INJECTED_PARAMS:
            # Token-like injected params: use context value, default to "".
            call_kwargs[pname] = ctx.tokens.get(pname, "")
        elif pname in provided:
            call_kwargs[pname] = provided[pname]
        elif pname == "request":
            call_kwargs[pname] = state.request

    result = fn(**call_kwargs)
    if inspect.isawaitable(result):
        result = await result
    return result


async def _call(
    fn: Optional[Callable],
    ctx: AgentContext,
    state: ContinuumState,
    **kwargs: Any,
) -> Any:
    """Convenience wrapper: invoke a skill if present, else return None."""
    if fn is None:
        return None
    return await invoke_skill(fn, kwargs, ctx, state)


# --------------------------------------------------------------------------- #
# LLM tool-calling loop
# --------------------------------------------------------------------------- #
def _parse_json(content: str) -> dict:
    """Best-effort parse of an LLM JSON answer (tolerates fences / stray prose)."""
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"result": parsed}
    except Exception:  # noqa: BLE001
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(text[start : end + 1])
            except Exception:  # noqa: BLE001
                pass
    logger.warning("Could not parse agent JSON output; returning empty result")
    return {}


async def _run_llm(
    model: Any,
    spec: dict,
    state: ContinuumState,
    skills: Dict[str, Optional[Callable]],
    ctx: AgentContext,
) -> dict:
    """Run the agent against a live model with its skills bound as tools."""
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage  # lazy

    tool_fns = {name: fn for name, fn in skills.items() if fn is not None}
    messages: List[Any] = [
        SystemMessage(content=build_system_prompt(spec, state)),
        HumanMessage(content=f"Execute your task for the request: {state.request}"),
    ]

    runnable = model
    if tool_fns:
        runnable = model.bind_tools([_tool_schema(n, f) for n, f in tool_fns.items()])

    response: Any = None
    for _ in range(MAX_TOOL_ITERS):
        response = await runnable.ainvoke(messages)
        messages.append(response)
        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            return _parse_json(getattr(response, "content", "") or "")
        for call in tool_calls:
            fn = tool_fns.get(call["name"])
            if fn is None:
                output: Any = {"error": f"unknown tool '{call['name']}'"}
            else:
                output = await invoke_skill(fn, call.get("args", {}) or {}, ctx, state)
            messages.append(
                ToolMessage(content=json.dumps(output, default=str), tool_call_id=call["id"])
            )

    logger.warning("Tool loop exhausted after %d iterations", MAX_TOOL_ITERS)
    return _parse_json(getattr(response, "content", "") or "")


# --------------------------------------------------------------------------- #
# Offline (deterministic) path — calls the agent's primary skills directly
# --------------------------------------------------------------------------- #
def _title_from_request(request: str) -> str:
    """Derive a short story title from a request."""
    words = request.strip().split()
    return " ".join(words[:8]).rstrip(".,") or "Feature"


async def _run_offline(
    role: str,
    state: ContinuumState,
    skills: Dict[str, Optional[Callable]],
    ctx: AgentContext,
) -> dict:
    """Produce artifacts by calling the agent's primary skills directly."""
    if role == AgentRole.BSA.value:
        ticket = await _call(skills.get("create_story"), ctx, state, request=state.request) or {}
        story = {
            "title": _title_from_request(state.request),
            "description": state.request,
            "acceptance_criteria": [],
            "ticket": ticket,
        }
        spec = await _call(skills.get("write_spec"), ctx, state, request=state.request, story=story) or {}
        return {"story": story, "spec": spec}

    if role == AgentRole.ARCHITECT.value:
        story = state.story or {}
        spec = (state.story or {}).get("spec", {})
        contract = await _call(skills.get("emit_contract"), ctx, state, story=story, spec=spec) or ""
        schema = await _call(skills.get("emit_schema"), ctx, state, story=story, spec=spec) or ""
        components = [
            {"name": "api", "type": "backend", "responsibility": "Serve the contract"},
            {"name": "db", "type": "database", "responsibility": "Persist data"},
        ]
        dag = (
            await _call(
                skills.get("write_dag"),
                ctx,
                state,
                story=story,
                contract=contract,
                schema=schema,
                components=components,
            )
            or {}
        )
        return {"contract": contract, "schema": schema, "components": components, "dag": dag}

    if role == AgentRole.PLANNER.value:
        feature_id = (state.dag or {}).get("dag_id", "dag-1")
        queried = await _call(skills.get("query_dag"), ctx, state, feature_id=feature_id) or {}
        checklist = await _call(skills.get("make_checklist"), ctx, state, dag=queried) or {}
        return {
            "tasks": queried.get("all_tasks", []),
            "parallel_groups": queried.get("parallel_groups", []),
            "checklist": checklist,
        }

    if role == AgentRole.DEVELOPER.value:
        code = (
            await _call(
                skills.get("write_code"),
                ctx,
                state,
                contract=state.contract or "",
                schema=state.schema or "",
            )
            or {}
        )
        return {"code": code}

    if role == AgentRole.SECURITY.value:
        sast = await _call(skills.get("run_sast"), ctx, state, code_path=ctx.repo_path) or {}
        return {"issues": sast.get("issues", []), "pass": bool(sast.get("clean", True))}

    return {}


# --------------------------------------------------------------------------- #
# Map agent output onto state
# --------------------------------------------------------------------------- #
def _set_gate(state: ContinuumState, name: str, status: str, error: Optional[str]) -> None:
    """Create or update a named gate on the state."""
    gate = next((g for g in state.gates if g.name == name), None)
    if gate is None:
        gate = GateStatus(name=name, status=status)
        state.gates.append(gate)
    gate.status = status
    gate.error_message = error
    gate.last_checked = time.time()


def _files_from(value: Any) -> Dict[str, str]:
    """Normalise code/tests output (dict or list of {file_path, content}) -> dict."""
    files: Dict[str, str] = {}
    if isinstance(value, dict):
        files.update({str(k): str(v) for k, v in value.items()})
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and "file_path" in item:
                files[str(item["file_path"])] = str(item.get("content", ""))
    return files


def apply_agent_output(state: ContinuumState, role: str, data: dict) -> None:
    """Merge an agent's structured output into ContinuumState."""
    if not data:
        return

    if role == AgentRole.BSA.value:
        if data.get("story"):
            story = dict(data["story"])
            if data.get("spec"):
                story["spec"] = data["spec"]
            state.story = story

    elif role == AgentRole.ARCHITECT.value:
        contract = data.get("contract")
        if isinstance(contract, str):
            state.contract = contract
        elif contract is not None:
            state.contract = yaml.safe_dump(contract)
        if data.get("schema"):
            state.schema = data["schema"]
        dag: Dict[str, Any] = dict(state.dag or {})
        if isinstance(data.get("dag"), dict):
            dag.update(data["dag"])
        elif data.get("dag") is not None:
            dag["raw"] = data["dag"]
        if data.get("components"):
            dag["components"] = data["components"]
        if dag:
            state.dag = dag

    elif role == AgentRole.PLANNER.value:
        state.dag = {**(state.dag or {}), "plan": data}

    elif role == AgentRole.DEVELOPER.value:
        files = _files_from(data.get("code"))
        files.update(_files_from(data.get("tests")))
        if files:
            state.code = {**(state.code or {}), **files}

    # SECURITY: the security_sast gate is now run as a real post-gate
    # (see `_run_post_gates`); no state mutation here.


# --------------------------------------------------------------------------- #
# Post-agent gates — deterministic sensors run after the agent finishes.
# --------------------------------------------------------------------------- #
async def _run_post_gates(role: str, state: ContinuumState, ctx: AgentContext) -> None:
    """
    After certain agents, run the matching deterministic gate and record its
    GateStatus on state. The orchestrator's `_route()` consumes these to
    decide retry / escalate (Rule 4: max 3 retries, then human_approval).
    """
    from . import gates  # local import: keep agent_runner usable without gates module loaded

    if role == AgentRole.DEVELOPER.value:
        target = _sandbox_or_local_path(state, ctx)
        passed, output = await gates.gate_local_verify(target)
        gates.update_gate_status(state, "local_verify", passed, output)
        logger.info("[GATE] local_verify=%s (%s)", "green" if passed else "red", target)

    elif role == AgentRole.SECURITY.value:
        target = _sandbox_or_local_path(state, ctx)
        passed, output = await gates.gate_sast(target)
        gates.update_gate_status(state, "security_sast", passed, output)
        logger.info("[GATE] security_sast=%s (%s)", "green" if passed else "red", target)

    elif role == AgentRole.ARCHITECT.value and state.contract:
        passed, output = await gates.gate_contract_validate(state.contract)
        gates.update_gate_status(state, "contract_validate", passed, output)
        logger.info("[GATE] contract_validate=%s", "green" if passed else "red")


def _sandbox_or_local_path(state: ContinuumState, ctx: AgentContext) -> str:
    """
    Resolve where the dev/security gate should look.

    M0: if the developer wrote files to state.code, they haven't been
    materialised on disk, so the gate runs against the repo (ctx.repo_path).
    Real sandbox execution will replace this with a sandbox workspace path.
    """
    return ctx.repo_path or "."


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
async def run_agent(
    state: ContinuumState,
    role: str,
    ctx: Optional[AgentContext] = None,
) -> ContinuumState:
    """
    Execute a single agent and merge its result into state.

    Args:
        state: The current orchestration state (mutated in place and returned).
        role: Agent role name matching `agents/<role>.yaml` (e.g. "bsa").
        ctx: Shared run context (driver, sandbox, tokens). Built from env if None.

    Returns:
        The updated ContinuumState (with `current_agent` set to this role).
    """
    ctx = ctx or AgentContext.from_env()
    started = time.time()
    logger.info("[%s] Starting agent run", role.upper())

    try:
        spec = load_agent_spec(role)
    except FileNotFoundError as exc:
        logger.warning("[%s] No spec found (%s); skipping (M1+ agent)", role.upper(), exc)
        state.current_agent = AgentRole(role)
        return state

    skills = {name: load_skill(name) for name in spec.get("allowed_skills", [])}
    available = [name for name, fn in skills.items() if fn is not None]
    logger.info("[%s] Skills available: %s", role.upper(), available)

    model = resolve_model(spec)
    data: dict = {}
    try:
        if model is not None:
            data = await _run_llm(model, spec, state, skills, ctx)
        else:
            data = await _run_offline(role, state, skills, ctx)
    except Exception as exc:  # noqa: BLE001 - never crash the graph on agent failure
        logger.exception("[%s] Agent run failed (%s); attempting offline fallback", role.upper(), exc)
        try:
            data = await _run_offline(role, state, skills, ctx)
        except Exception as exc2:  # noqa: BLE001
            logger.error("[%s] Offline fallback also failed: %s", role.upper(), exc2)
            state.error_message = f"{role} failed: {exc2}"
            data = {}

    apply_agent_output(state, role, data)
    state.current_agent = AgentRole(role)

    # Deterministic gates run *after* the agent. The orchestrator's _route()
    # picks up the resulting GateStatus to drive retry / escalate logic.
    try:
        await _run_post_gates(role, state, ctx)
    except Exception as exc:  # noqa: BLE001 - gate failure must not crash the run
        logger.exception("[%s] Post-gate failed: %s", role.upper(), exc)

    state.messages.append(
        AgentMessage(
            agent=AgentRole(role),
            timestamp=time.time(),
            content=json.dumps(data, default=str)[:4000],
        )
    )
    logger.info("[%s] Complete in %.2fs", role.upper(), time.time() - started)
    return state
