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

# --------------------------------------------------------------------------- #
# M6: cost accounting
# --------------------------------------------------------------------------- #
# Fixed per-agent estimate used on the offline path (no LLM call is made, so
# there is no real token usage). Keeps state.cost_usd populated so the Work
# Queue / RunMetrics never show a blank cost. Pure bookkeeping — only ever adds.
_OFFLINE_COST_PER_AGENT = 0.02

# Per-1K-token pricing by model tier (USD). Best-effort; used only when a live
# response surfaces token usage. Values are deliberately conservative defaults.
_PRICING_PER_1K = {
    "strong": {"prompt": 0.005, "completion": 0.015},
    "cheap":  {"prompt": 0.0005, "completion": 0.0015},
}

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
    run_id: str = ""  # set by the API layer; empty in offline / test paths

    @classmethod
    def from_env(cls) -> "AgentContext":
        """
        Build a context from environment variables (best-effort, no I/O).

        Neo4j driver is only created when NEO4J_URI or NEO4J_PASSWORD is set;
        otherwise it stays None so skill fallback paths are used.
        """
        ado = (
            os.getenv("AZURE_DEVOPS_TOKEN", "")
            or os.getenv("AZURE_DEVOPS_PAT", "")
        )
        # Lazy-load Neo4j driver from config module to avoid circular imports
        neo4j_driver = None
        try:
            from config import get_neo4j_driver, NEO4J_CONFIGURED  # type: ignore
            if NEO4J_CONFIGURED:
                neo4j_driver = get_neo4j_driver()
        except Exception:  # noqa: BLE001
            pass  # config module not yet initialized or neo4j not installed

        return cls(
            neo4j_driver=neo4j_driver,
            repo_path=os.getenv("REPO_PATH", "."),
            tokens={"jira_token": ado, "ado_token": ado, "auth_token": ado},
            run_id=os.getenv("CONTINUUM_RUN_ID", ""),
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
# Model resolution  (M1: langchain-azure-ai 1.2.x)
# --------------------------------------------------------------------------- #
def _deployment_for_tier(tier: str) -> Optional[str]:
    """Map a model tier to an Azure deployment id from the environment."""
    if tier == "cheap":
        return os.getenv("AZURE_DEPLOYMENT_CHEAP") or os.getenv("AZURE_DEPLOYMENT_ID")
    return os.getenv("AZURE_DEPLOYMENT_STRONG") or os.getenv("AZURE_DEPLOYMENT_ID")


def _azure_endpoint() -> Optional[str]:
    """Return the Azure AI endpoint from env, normalised (no trailing slash)."""
    ep = os.getenv("AZURE_AI_ENDPOINT") or os.getenv("AZURE_OPENAI_ENDPOINT") or ""
    return ep.rstrip("/") or None


def resolve_model(spec: dict) -> Optional[Any]:
    """
    Return a LangChain chat model for the agent's tier, or None to trigger
    the deterministic offline path.

    M1 (langchain-azure-ai 1.2.x) — two credential paths:
      1. AZURE_AI_ENDPOINT + managed identity  (DefaultAzureCredential)
      2. AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT  (key-based, dev-only)

    If neither is configured, or if the import/instantiation fails for any
    reason, we fall back to offline. This ensures the offline path is always
    reachable (11/11 must pass without credentials).
    """
    has_key = bool(os.getenv("AZURE_OPENAI_API_KEY"))
    has_endpoint = bool(_azure_endpoint())
    has_mi = bool(os.getenv("AZURE_CLIENT_ID") or os.getenv("AZURE_INFERENCE_CREDENTIAL"))

    if not (has_key or has_endpoint or has_mi):
        logger.info("[MODEL] No Azure credentials found — using offline path")
        return None

    tier = spec.get("model_tier", "strong")
    deployment = _deployment_for_tier(tier)
    if not deployment:
        logger.warning("[MODEL] No deployment env var for tier '%s' — offline path", tier)
        return None

    try:
        # langchain-azure-ai 1.2.x preferred import path
        from langchain_azure_ai.chat_models import AzureChatCompletions  # type: ignore[import]

        endpoint = _azure_endpoint()
        api_key = os.getenv("AZURE_OPENAI_API_KEY")

        if api_key and endpoint:
            # Key-based: fastest for local dev
            model = AzureChatCompletions(
                azure_endpoint=endpoint,
                azure_deployment=deployment,
                api_key=api_key,
                api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
                temperature=spec.get("temperature", 0.5),
                max_tokens=spec.get("max_tokens", 2000),
            )
        else:
            # Managed-identity / DefaultAzureCredential path
            from azure.identity import DefaultAzureCredential, get_bearer_token_provider  # type: ignore[import]
            token_provider = get_bearer_token_provider(
                DefaultAzureCredential(),
                "https://cognitiveservices.azure.com/.default",
            )
            model = AzureChatCompletions(
                azure_endpoint=endpoint or "",
                azure_deployment=deployment,
                azure_ad_token_provider=token_provider,
                api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
                temperature=spec.get("temperature", 0.5),
                max_tokens=spec.get("max_tokens", 2000),
            )

        logger.info("[MODEL] Resolved %s model: %s (tier=%s)", "live", deployment, tier)
        return model

    except ImportError as exc:
        logger.warning("[MODEL] langchain-azure-ai not installed (%s) — offline path", exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("[MODEL] Could not init model '%s': %s — offline path", deployment, exc)
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
    """
    Run the agent against a live model with its skills bound as tools.

    M1 improvements over M0:
    - Retries transient HTTP errors (429 rate-limit, 5xx) up to 3×.
    - Tool-call errors are returned as {"error": ...} ToolMessages so the LLM
      can self-correct rather than crashing the loop.
    - Final answer is extracted even if the last response still has tool_calls
      (LLM didn't finish cleanly) — we force-parse what we have.
    """
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage  # lazy

    tool_fns = {name: fn for name, fn in skills.items() if fn is not None}
    system_prompt = build_system_prompt(spec, state)
    messages: List[Any] = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"Execute your task for the request: {state.request}"),
    ]

    runnable = model
    if tool_fns:
        runnable = model.bind_tools([_tool_schema(n, f) for n, f in tool_fns.items()])

    response: Any = None
    _MAX_RETRIES = 3

    for iteration in range(MAX_TOOL_ITERS):
        # --- invoke with retry on transient errors ---
        for attempt in range(_MAX_RETRIES):
            try:
                response = await runnable.ainvoke(messages)
                break
            except Exception as exc:  # noqa: BLE001
                err_str = str(exc).lower()
                is_transient = any(x in err_str for x in ("429", "503", "502", "rate", "timeout"))
                if is_transient and attempt < _MAX_RETRIES - 1:
                    wait = 2 ** attempt          # 1s, 2s, 4s back-off
                    logger.warning(
                        "[LLM] Transient error (attempt %d/%d): %s — retrying in %ds",
                        attempt + 1, _MAX_RETRIES, exc, wait,
                    )
                    import asyncio as _asyncio
                    await _asyncio.sleep(wait)
                else:
                    raise  # non-transient or out of retries → bubble up

        messages.append(response)
        tool_calls = getattr(response, "tool_calls", None) or []

        if not tool_calls:
            # Clean final answer
            parsed = _parse_json(getattr(response, "content", "") or "")
            return _attach_usage(parsed, response)

        # Execute tool calls; errors become ToolMessage content so LLM can react
        for call in tool_calls:
            tool_id = call.get("id") or f"call_{iteration}"
            fn = tool_fns.get(call["name"])
            if fn is None:
                output: Any = {"error": f"unknown tool '{call['name']}'"}
                logger.warning("[LLM] Unknown tool '%s' requested by model", call["name"])
            else:
                try:
                    output = await invoke_skill(fn, call.get("args", {}) or {}, ctx, state)
                except Exception as exc:  # noqa: BLE001
                    output = {"error": str(exc)}
                    logger.warning("[LLM] Tool '%s' raised: %s", call["name"], exc)
            messages.append(
                ToolMessage(
                    content=json.dumps(output, default=str),
                    tool_call_id=tool_id,
                )
            )

    # Loop exhausted — extract best-effort answer from last response
    logger.warning("[LLM] Tool loop exhausted after %d iterations — forcing final parse", MAX_TOOL_ITERS)
    return _attach_usage(_parse_json(getattr(response, "content", "") or ""), response)


def _attach_usage(parsed: dict, response: Any) -> dict:
    """
    Stash token usage from a LangChain response under a private key (M6).

    run_agent() pops `__usage__` before applying agent output and feeds it to
    _accrue_cost(). Returns `parsed` unchanged when no usage is exposed, so the
    offline path (which never calls this) is unaffected.
    """
    try:
        meta = getattr(response, "response_metadata", None) or {}
        usage = meta.get("token_usage") or meta.get("usage")
        if not usage:
            usage = getattr(response, "usage_metadata", None)
        if usage:
            parsed.setdefault("__usage__", usage)
    except Exception:  # noqa: BLE001
        pass
    return parsed


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
        # M3: GraphRAG — pull relevant past episodes to ground the story.
        past_episodes = (
            await _call(skills.get("graphrag_query"), ctx, state, query=state.request) or []
        )

        ticket = await _call(skills.get("create_story"), ctx, state, request=state.request) or {}
        story = {
            "title": _title_from_request(state.request),
            "description": state.request,
            "acceptance_criteria": [],
            "ticket": ticket,
        }
        spec = await _call(skills.get("write_spec"), ctx, state, request=state.request, story=story) or {}
        result: Dict[str, Any] = {"story": story, "spec": spec}
        if past_episodes:
            result["episodes"] = past_episodes
            logger.info("[BSA] Grounded on %d past episode(s) via GraphRAG", len(past_episodes))

        # M11: Spec Registry — retrieve the prior agreed spec for this component
        # BEFORE drafting context is finalized, then persist this run's spec as a
        # new version (recording a supersession if it materially differs).
        from orchestrator.component import component_slug
        component = component_slug(state.request, story)
        registry = await _call(skills.get("query_spec_registry"), ctx, state, component=component) or {}
        prior = registry.get("current")
        write_res = await _call(
            skills.get("write_spec_registry"), ctx, state,
            component=component, spec_body=spec, request=state.request,
            run_id=state.run_id or "", prior_spec=prior,
            supersedes_reason=f"Run {state.run_id or 'n/a'} updated spec for {component}",
        ) or {}
        result["component"] = component
        result["registry_specs"] = registry.get("history", [])
        result["registry_current_before"] = prior
        result["spec_superseded"] = write_res.get("superseded")
        if prior is not None:
            logger.info("[BSA] M11: grounded on Registry spec v%s for %s", prior.get("version"), component)
        return result

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

        tasks = queried.get("all_tasks", [])
        # M5: if the DAG has >= 5 tasks, generate a decomposition script for operator use.
        decomposition_script: Optional[str] = None
        if len(tasks) >= 5:
            try:
                from orchestrator.decomposer import generate_script
                decomposition_script = generate_script(state.dag or {})
                logger.info("[PLANNER] DAG has %d tasks — decomposition script generated", len(tasks))
            except Exception as exc:  # noqa: BLE001
                logger.warning("[PLANNER] Decomposer failed: %s", exc)

        result: Dict[str, Any] = {
            "tasks": tasks,
            "parallel_groups": queried.get("parallel_groups", []),
            "checklist": checklist,
        }
        if decomposition_script:
            result["decomposition_script"] = decomposition_script
        return result

    if role == AgentRole.DEVELOPER.value:
        code = (
            await _call(
                skills.get("write_code"),
                ctx,
                state,
                contract=state.contract or "",
                schema=state.schema or "",
                language="python",
            )
            or {}
        )
        return {"code": code}

    # --- M1: developer sub-agents (T3) ---
    if role == AgentRole.DATABASE.value:
        schema_code = (
            await _call(
                skills.get("emit_schema"),
                ctx,
                state,
                story=state.story or {},
                spec=(state.story or {}).get("spec", {}),
            )
            or ""
        )
        migration = schema_code if isinstance(schema_code, str) else ""
        return {
            "code": {
                "migrations/001_initial.sql": migration or "-- auto-generated migration placeholder",
                "seeds/001_reference_data.sql": "-- seed data placeholder",
            }
        }

    if role == AgentRole.BACKEND.value:
        code = (
            await _call(
                skills.get("write_code"),
                ctx,
                state,
                contract=state.contract or "",
                schema=state.schema or "",
                language="python",
            )
            or {}
        )
        return {"code": code}

    if role == AgentRole.FRONTEND.value:
        resource = _resource_from_contract(state.contract or "")
        code = {
            "app/layout.tsx": _fe_layout(),
            "app/page.tsx":   _fe_home(resource),
            f"app/{resource}s/page.tsx": _fe_list_page(resource),
            f"app/{resource}s/[id]/page.tsx": _fe_detail_page(resource),
            f"components/{resource.capitalize()}List.tsx": _fe_list_component(resource),
            f"components/{resource.capitalize()}Form.tsx": _fe_form_component(resource),
            "lib/api.ts": _fe_api_client(resource),
            "lib/types.ts": _fe_types(resource),
        }
        return {"code": code}

    if role == AgentRole.SECURITY.value:
        sast = await _call(skills.get("run_sast"), ctx, state, code_path=ctx.repo_path) or {}
        return {"issues": sast.get("issues", []), "pass": bool(sast.get("clean", True))}

    # M3: Memory agent — write episode summarising the completed run.
    if role == AgentRole.MEMORY.value:
        feature_id = (state.dag or {}).get("dag_id") or state.run_id or "offline"
        story_id = (state.story or {}).get("ticket", {}).get("story_id", "")
        outcome = "completed" if not state.error_message else f"failed: {state.error_message}"

        # Gate summary for the outcome field.
        gate_summary = "; ".join(
            f"{g.name}={g.status}" + (f"(retry={g.retry_count})" if g.retry_count else "")
            for g in state.gates
        )
        if gate_summary:
            outcome = f"{outcome} | gates: {gate_summary}"

        # Files touched (entities).
        entities = [
            {"name": f, "type": "file"}
            for f in list((state.code or {}).keys())[:20]  # cap at 20 to avoid huge payloads
        ]

        ep = await _call(
            skills.get("write_episode"),
            ctx,
            state,
            feature_id=feature_id,
            agent="pipeline",
            decision=f"Implemented: {state.request[:200]}",
            action="ran_pipeline",
            outcome=outcome,
            request_text=state.request,
            story_id=story_id,
            entities=entities,
        ) or {}

        return {"episode": ep, "episodes_written": 1}

    # M5: Evolution Agent — observe telemetry, diagnose patterns, propose a change.
    if role == AgentRole.EVOLUTION.value:
        try:
            telemetry = await _call(skills.get("read_telemetry"), ctx, state) or {}
        except Exception:  # noqa: BLE001
            telemetry = {}

        from evolution.agent import EvolutionAgent
        evo_agent = EvolutionAgent()
        patterns = evo_agent.observe()
        diagnoses = evo_agent.diagnose(patterns)
        proposal = evo_agent.propose(diagnoses) if diagnoses else {}

        return {
            "telemetry": telemetry,
            "patterns_found": len(patterns),
            "proposal": proposal,
            "action": "proposal_written" if proposal else "no_action",
        }

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


def _accrue_cost(state: ContinuumState, spec: dict, usage: Optional[Dict[str, Any]]) -> None:
    """
    Add this agent run's estimated model cost to state.cost_usd (M6).

    - Offline (usage is None / empty): add a fixed per-agent estimate so the
      field is always populated without any LLM call.
    - Live: if a token-usage dict is present, price prompt/completion tokens by
      the agent's model tier; otherwise fall back to the fixed estimate.

    Must never raise — cost tracking is non-essential and must not break a run.
    """
    try:
        if not usage:
            state.cost_usd = round(state.cost_usd + _OFFLINE_COST_PER_AGENT, 6)
            return
        tier = spec.get("model_tier", "strong")
        rates = _PRICING_PER_1K.get(tier, _PRICING_PER_1K["strong"])
        prompt_tokens = float(
            usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        )
        completion_tokens = float(
            usage.get("completion_tokens") or usage.get("output_tokens") or 0
        )
        cost = (
            prompt_tokens / 1000.0 * rates["prompt"]
            + completion_tokens / 1000.0 * rates["completion"]
        )
        if cost <= 0:
            cost = _OFFLINE_COST_PER_AGENT
        state.cost_usd = round(state.cost_usd + cost, 6)
    except Exception:  # noqa: BLE001 — cost accounting must never crash a run
        state.cost_usd = round(state.cost_usd + _OFFLINE_COST_PER_AGENT, 6)


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
        # M3: store past episodes retrieved by GraphRAG.
        if data.get("episodes") is not None:
            state.episodes = data["episodes"]
        # M11: Spec Registry results.
        if data.get("component"):
            state.component = data["component"]
        if data.get("registry_specs") is not None:
            state.registry_specs = data["registry_specs"]
        state.registry_current_before = data.get("registry_current_before")
        state.spec_superseded = data.get("spec_superseded")

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
        plan_data = {k: v for k, v in data.items() if k != "decomposition_script"}
        state.dag = {**(state.dag or {}), "plan": plan_data}
        # M5: store decomposition script when generated.
        if data.get("decomposition_script"):
            state.decomposition_script = data["decomposition_script"]

    elif role in (
        AgentRole.DEVELOPER.value,
        AgentRole.DATABASE.value,
        AgentRole.BACKEND.value,
        AgentRole.FRONTEND.value,
    ):
        # All developer sub-agents merge their files into state.code
        files = _files_from(data.get("code"))
        files.update(_files_from(data.get("tests")))
        if files:
            state.code = {**(state.code or {}), **files}

    elif role == AgentRole.SECURITY.value:
        # Fallback gate record from the skill's structured output — this ensures
        # apply_agent_output() alone sets a GateStatus (needed by unit tests that
        # call apply_agent_output directly, without going through run_agent /
        # _run_post_gates). The real post-gate in _run_post_gates() may
        # subsequently overwrite this with bandit/semgrep results.
        sec_pass = bool(data.get("pass", True))
        _set_gate(
            state,
            "security_sast",
            "green" if sec_pass else "red",
            None if sec_pass else str(data.get("issues", [])),
        )

    elif role == AgentRole.MEMORY.value:
        # M3: accumulate episodes written by the Memory agent.
        if data.get("episode"):
            state.episodes_written = list(state.episodes_written or [])
            state.episodes_written.append(data["episode"])

    elif role == AgentRole.EVOLUTION.value:
        # M5: telemetry + proposals are informational; no state mutation needed.
        pass


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
    from .events import event_bus  # lazy to keep offline path free of event overhead

    async def _gate_event(name: str, passed: bool, output: str) -> None:
        if ctx.run_id:
            await event_bus.emit(ctx.run_id, {
                "event_type": "gate_green" if passed else "gate_red",
                "agent": role,
                "gate": name,
                "data": {"output": (output or "")[:500]},
            })

    if role == AgentRole.DEVELOPER.value:
        target = _sandbox_or_local_path(state, ctx)
        passed, output = await gates.gate_local_verify(target)
        gates.update_gate_status(state, "local_verify", passed, output)
        await _gate_event("local_verify", passed, output)
        logger.info("[GATE] local_verify=%s (%s)", "green" if passed else "red", target)

    elif role == AgentRole.SECURITY.value:
        target = _sandbox_or_local_path(state, ctx)
        passed, output = await gates.gate_sast(target)
        gates.update_gate_status(state, "security_sast", passed, output)
        await _gate_event("security_sast", passed, output)
        logger.info("[GATE] security_sast=%s (%s)", "green" if passed else "red", target)

    elif role == AgentRole.ARCHITECT.value and state.contract:
        passed, output = await gates.gate_contract_validate(state.contract)
        gates.update_gate_status(state, "contract_validate", passed, output)
        await _gate_event("contract_validate", passed, output)
        logger.info("[GATE] contract_validate=%s", "green" if passed else "red")


# --------------------------------------------------------------------------- #
# Frontend scaffold helpers (used by FRONTEND offline path)
# --------------------------------------------------------------------------- #
def _resource_from_contract(contract: str) -> str:
    """Derive the primary resource name from the OpenAPI contract."""
    import re
    m = re.search(r"^\s{2}/([a-z][a-z0-9_-]+)s?:", contract, re.MULTILINE)
    if m:
        return m.group(1).rstrip("s")
    return "item"


def _fe_layout() -> str:
    return '''\
import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = { title: "Continuum App", description: "Generated by Continuum" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-background font-sans antialiased">
        <main className="container mx-auto py-8">{children}</main>
      </body>
    </html>
  );
}
'''


def _fe_home(resource: str) -> str:
    cap = resource.capitalize()
    return f'''\
import Link from "next/link";

export default function Home() {{
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-3xl font-bold">Continuum Dashboard</h1>
      <Link href="/{resource}s" className="text-blue-600 underline">
        View all {cap}s
      </Link>
    </div>
  );
}}
'''


def _fe_list_page(resource: str) -> str:
    cap = resource.capitalize()
    return f'''\
"use client";
import {{ {cap}List }} from "@/components/{cap}List";

export default function {cap}sPage() {{
  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">{cap}s</h1>
      <{cap}List />
    </div>
  );
}}
'''


def _fe_detail_page(resource: str) -> str:
    cap = resource.capitalize()
    return f'''\
"use client";
import {{ use{cap} }} from "@/lib/api";

export default function {cap}DetailPage({{ params }}: {{ params: {{ id: string }} }}) {{
  const {{ data, isLoading, error }} = use{cap}(params.id);
  if (isLoading) return <p>Loading...</p>;
  if (error) return <p className="text-red-500">Error loading {resource}.</p>;
  if (!data) return <p>Not found.</p>;
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">{{data.name}}</h1>
      {{data.description && <p className="text-muted-foreground">{{data.description}}</p>}}
      <p className="text-sm text-muted-foreground">Created: {{new Date(data.created_at).toLocaleString()}}</p>
    </div>
  );
}}
'''


def _fe_list_component(resource: str) -> str:
    cap = resource.capitalize()
    return f'''\
"use client";
import useSWR from "swr";
import {{ fetcher }} from "@/lib/api";
import type {{ {cap} }} from "@/lib/types";
import Link from "next/link";

export function {cap}List() {{
  const {{ data, isLoading, error }} = useSWR<{{ items: {cap}[]; total: number }}>(
    "/api/{resource}s",
    fetcher
  );

  if (isLoading) return <p>Loading {resource}s…</p>;
  if (error) return <p className="text-red-500">Failed to load {resource}s.</p>;

  const items = data?.items ?? [];

  return (
    <div className="divide-y rounded-lg border">
      {{items.length === 0 && (
        <p className="p-4 text-muted-foreground">No {resource}s yet.</p>
      )}}
      {{items.map((item) => (
        <div key={{item.id}} className="flex items-center justify-between p-4">
          <Link href="/{resource}s/{{item.id}}" className="font-medium hover:underline">
            {{item.name}}
          </Link>
          <span className="text-sm text-muted-foreground">{{item.status}}</span>
        </div>
      ))}}
    </div>
  );
}}
'''


def _fe_form_component(resource: str) -> str:
    cap = resource.capitalize()
    return f'''\
"use client";
import {{ useState }} from "react";

interface Props {{
  onSuccess?: () => void;
}}

export function {cap}Form({{ onSuccess }}: Props) {{
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {{
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {{
      const res = await fetch("/api/{resource}s", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ name, description }}),
      }});
      if (!res.ok) throw new Error(await res.text());
      setName("");
      setDescription("");
      onSuccess?.();
    }} catch (err: unknown) {{
      setError(err instanceof Error ? err.message : "Unknown error");
    }} finally {{
      setLoading(false);
    }}
  }}

  return (
    <form onSubmit={{handleSubmit}} className="space-y-4">
      <div>
        <label htmlFor="name" className="block text-sm font-medium">Name *</label>
        <input
          id="name" value={{name}} onChange={{(e) => setName(e.target.value)}}
          required className="mt-1 block w-full rounded-md border px-3 py-2"
        />
      </div>
      <div>
        <label htmlFor="description" className="block text-sm font-medium">Description</label>
        <textarea
          id="description" value={{description}} onChange={{(e) => setDescription(e.target.value)}}
          rows={{3}} className="mt-1 block w-full rounded-md border px-3 py-2"
        />
      </div>
      {{error && <p className="text-red-500 text-sm">{{error}}</p>}}
      <button
        type="submit" disabled={{loading}}
        className="rounded-md bg-blue-600 px-4 py-2 text-white hover:bg-blue-700 disabled:opacity-50"
      >
        {{loading ? "Creating…" : "Create {cap}"}}
      </button>
    </form>
  );
}}
'''


def _fe_api_client(resource: str) -> str:
    cap = resource.capitalize()
    return f'''\
import useSWR from "swr";
import type {{ {cap}, {cap}Input, PaginatedResponse }} from "./types";

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function fetcher<T>(url: string): Promise<T> {{
  const res = await fetch(BASE_URL + url);
  if (!res.ok) throw new Error(`HTTP ${{res.status}}: ${{await res.text()}}`);
  return res.json() as Promise<T>;
}}

export function use{cap}s(page = 1, pageSize = 20) {{
  return useSWR<PaginatedResponse<{cap}>>(
    `/{resource}s?page=${{page}}&page_size=${{pageSize}}`,
    fetcher
  );
}}

export function use{cap}(id: string) {{
  return useSWR<{cap}>(id ? `/{resource}s/${{id}}` : null, fetcher);
}}

export async function create{cap}(data: {cap}Input): Promise<{cap}> {{
  const res = await fetch(`${{BASE_URL}}/{resource}s`, {{
    method: "POST",
    headers: {{ "Content-Type": "application/json" }},
    body: JSON.stringify(data),
  }});
  if (!res.ok) throw new Error(`Create failed: ${{await res.text()}}`);
  return res.json();
}}

export async function update{cap}(id: string, data: {cap}Input): Promise<{cap}> {{
  const res = await fetch(`${{BASE_URL}}/{resource}s/${{id}}`, {{
    method: "PUT",
    headers: {{ "Content-Type": "application/json" }},
    body: JSON.stringify(data),
  }});
  if (!res.ok) throw new Error(`Update failed: ${{await res.text()}}`);
  return res.json();
}}

export async function delete{cap}(id: string): Promise<void> {{
  const res = await fetch(`${{BASE_URL}}/{resource}s/${{id}}`, {{ method: "DELETE" }});
  if (!res.ok && res.status !== 204) throw new Error(`Delete failed: ${{await res.text()}}`);
}}
'''


def _fe_types(resource: str) -> str:
    cap = resource.capitalize()
    return f'''\
export interface {cap} {{
  id: string;
  name: string;
  description: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}}

export interface {cap}Input {{
  name: string;
  description?: string | null;
}}

export interface PaginatedResponse<T> {{
  items: T[];
  total: number;
  page: number;
  page_size: number;
}}
'''


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
    run_id = ctx.run_id or (state.run_id or "")
    started = time.time()
    logger.info("[%s] Starting agent run", role.upper())

    # --- Emit agent_start ---
    if run_id:
        from .events import event_bus  # lazy import so offline path stays clean
        await event_bus.emit(run_id, {
            "event_type": "agent_start",
            "agent": role,
            "run_id": run_id,
            "data": {},
        })

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
            # M9: opted-in roles run on the Microsoft Agent Framework pilot.
            # Any MAF problem degrades to the LangChain loop (then offline below).
            from .maf_runner import MAFUnavailable, run_maf_agent, should_use_maf
            if should_use_maf(role):
                try:
                    data = await run_maf_agent(spec, state, skills, ctx)
                except MAFUnavailable as exc:
                    logger.warning(
                        "[%s] MAF unavailable (%s) — falling back to LangChain loop",
                        role.upper(), exc,
                    )
                    data = await _run_llm(model, spec, state, skills, ctx)
            else:
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

    # M6: account for model cost before merging output (pop the private usage
    # key so it never leaks into apply_agent_output / artifacts).
    usage = data.pop("__usage__", None) if isinstance(data, dict) else None
    _accrue_cost(state, spec, usage)

    apply_agent_output(state, role, data)
    state.current_agent = AgentRole(role)

    # Deterministic gates run *after* the agent. The orchestrator's _route()
    # picks up the resulting GateStatus to drive retry / escalate logic.
    try:
        await _run_post_gates(role, state, ctx)
    except Exception as exc:  # noqa: BLE001 - gate failure must not crash the run
        logger.exception("[%s] Post-gate failed: %s", role.upper(), exc)

    elapsed = time.time() - started
    state.messages.append(
        AgentMessage(
            agent=AgentRole(role),
            timestamp=time.time(),
            content=json.dumps(data, default=str)[:4000],
        )
    )

    # --- Emit agent_complete ---
    if run_id:
        from .events import event_bus  # already imported above but kept lazy per usage
        await event_bus.emit(run_id, {
            "event_type": "agent_complete",
            "agent": role,
            "run_id": run_id,
            "data": {
                "duration_s": round(elapsed, 2),
                "artifact_keys": _artifact_summary(role, state),
            },
        })

    logger.info("[%s] Complete in %.2fs", role.upper(), elapsed)
    return state


def _artifact_summary(role: str, state: ContinuumState) -> List[str]:
    """Return the keys of artifacts this role produced (for the UI artifact viewer)."""
    mapping: Dict[str, List[str]] = {
        AgentRole.BSA.value:       ["story"],
        AgentRole.ARCHITECT.value: ["contract", "schema", "dag"],
        AgentRole.PLANNER.value:   ["dag.plan"],
        AgentRole.DEVELOPER.value: ["code"],
        AgentRole.SECURITY.value:  ["gates.security_sast"],
        AgentRole.MEMORY.value:    ["episodes_written"],
        AgentRole.EVOLUTION.value: ["proposal"],
    }
    return mapping.get(role, [])
