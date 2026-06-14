# File: continuum/orchestrator/maf_runner.py
"""
M9: Microsoft Agent Framework (MAF) harness pilot.

Continuum agents normally run through the hand-rolled LangChain tool-call loop
in `agent_runner._run_llm` (MAX_TOOL_ITERS=5). This module pilots an alternative
execution path for *one* opted-in agent (the Backend agent) on the Microsoft
Agent Framework — letting MAF own the tool-calling loop instead.

## Non-negotiable invariants honoured here

- **Offline path never touched.** MAF only ever engages when a live model is
  resolved *and* the role is explicitly opted in *and* the `agent_framework`
  package is importable. In the offline/thin environment all three are false, so
  this module is completely dormant and the deterministic path is unchanged.
- **Opt-in, default off.** The pilot is controlled by `CONTINUUM_MAF_AGENTS`
  (comma-separated roles, e.g. `backend`). Unset → no agent uses MAF.
- **Graceful degradation.** If MAF is requested but unavailable or fails at
  runtime, `run_agent` falls back to the existing LangChain loop, then to the
  offline path — a MAF problem can never break a run.

The MAF API surface targeted here is the published `agent-framework` package
(`pip install agent-framework`). Because that package is an optional, live-only
dependency, every MAF call is wrapped so any import/runtime failure raises
`MAFUnavailable` (or propagates) and the caller falls back.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, FrozenSet, Optional

logger = logging.getLogger(__name__)

# Env var: comma-separated agent roles to run on MAF (e.g. "backend" or
# "backend,frontend"). Empty / unset → the pilot is off for every agent.
MAF_ENV_FLAG = "CONTINUUM_MAF_AGENTS"

# Cache the package-availability probe so we only import once per process.
_maf_available_cache: Optional[bool] = None


class MAFUnavailable(RuntimeError):
    """Raised when the MAF path is requested but the framework cannot be used.

    The caller (`agent_runner.run_agent`) catches this and falls back to the
    LangChain tool-call loop, so a missing/broken MAF install is never fatal.
    """


def maf_enabled_roles() -> FrozenSet[str]:
    """Parse `CONTINUUM_MAF_AGENTS` into a set of normalised role names."""
    raw = os.getenv(MAF_ENV_FLAG, "") or ""
    return frozenset(r.strip().lower() for r in raw.split(",") if r.strip())


def maf_package_available() -> bool:
    """True iff the `agent_framework` package can be imported (cached)."""
    global _maf_available_cache
    if _maf_available_cache is None:
        try:
            import importlib.util

            _maf_available_cache = (
                importlib.util.find_spec("agent_framework") is not None
            )
        except Exception:  # noqa: BLE001 — probing must never raise
            _maf_available_cache = False
    return _maf_available_cache


def should_use_maf(role: str) -> bool:
    """
    Decide whether `role` should run on MAF.

    Requires BOTH: the role is opted in via `CONTINUUM_MAF_AGENTS`, AND the
    `agent_framework` package is importable. Either being false → use the
    existing LangChain path. (The caller additionally only consults this when a
    live model was resolved, so the offline path is never affected.)
    """
    return role.lower() in maf_enabled_roles() and maf_package_available()


def maf_tool_specs(skills: Dict[str, Optional[Callable]]) -> list:
    """
    Build OpenAI-style tool schemas for the agent's skills, reusing the same
    schema builder as the LangChain path so the tool surface is identical.

    Pure function — does not require `agent_framework` to be installed, so the
    adapter is unit-testable offline. Injected params are stripped by the shared
    `_tool_schema` helper.
    """
    from .agent_runner import _tool_schema  # lazy import: avoid import cycle

    specs = []
    for name, fn in skills.items():
        if fn is not None:
            specs.append(_tool_schema(name, fn))
    return specs


def _import_maf() -> Any:
    """Import the MAF Azure chat client, or raise MAFUnavailable."""
    try:
        from agent_framework.azure import AzureOpenAIChatClient  # type: ignore[import]

        return AzureOpenAIChatClient
    except Exception as exc:  # noqa: BLE001 — any import problem → fall back
        raise MAFUnavailable(f"agent_framework not importable: {exc}") from exc


def _build_maf_client(client_cls: Any, spec: dict) -> Any:
    """Construct a MAF Azure chat client from the same env vars as resolve_model."""
    endpoint = (
        os.getenv("AZURE_AI_ENDPOINT") or os.getenv("AZURE_OPENAI_ENDPOINT") or ""
    ).rstrip("/")
    tier = spec.get("model_tier", "strong")
    deployment = (
        os.getenv("AZURE_DEPLOYMENT_CHEAP") if tier == "cheap"
        else os.getenv("AZURE_DEPLOYMENT_STRONG")
    ) or os.getenv("AZURE_DEPLOYMENT_ID")
    api_key = os.getenv("AZURE_OPENAI_API_KEY")
    if not (endpoint and deployment):
        raise MAFUnavailable("Azure endpoint/deployment not configured for MAF")

    kwargs: Dict[str, Any] = {
        "endpoint": endpoint,
        "deployment_name": deployment,
        "api_version": os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
    }
    if api_key:
        kwargs["api_key"] = api_key
    try:
        return client_cls(**kwargs)
    except Exception as exc:  # noqa: BLE001
        raise MAFUnavailable(f"could not init MAF client: {exc}") from exc


async def run_maf_agent(
    spec: dict,
    state: Any,
    skills: Dict[str, Optional[Callable]],
    ctx: Any,
) -> dict:
    """
    Execute an agent on the Microsoft Agent Framework (M9 pilot).

    Wraps each allowed skill as a MAF tool, lets MAF drive the tool-calling loop,
    then parses the final JSON answer using the same parser as the LangChain path.

    Raises:
        MAFUnavailable: when the framework is not installed or cannot be
            initialised — the caller falls back to the LangChain loop.

    Returns:
        Parsed agent output dict (with `__usage__` token usage when MAF exposes
        it), identical in shape to `_run_llm`'s return value.
    """
    client_cls = _import_maf()  # MAFUnavailable if missing
    client = _build_maf_client(client_cls, spec)

    # Reuse the shared prompt + skill-invocation + parsing helpers.
    from .agent_runner import (  # lazy import: avoid import cycle
        _attach_usage,
        _parse_json,
        build_system_prompt,
        invoke_skill,
    )

    system_prompt = build_system_prompt(spec, state)
    user_prompt = f"Execute your task for the request: {state.request}"

    # Wrap each skill as a plain async callable MAF can introspect as a tool.
    # The closure injects ctx/state and forwards LLM-provided args via invoke_skill.
    def _make_tool(skill_name: str, fn: Callable) -> Callable:
        async def _tool(**kwargs: Any) -> Any:
            return await invoke_skill(fn, kwargs, ctx, state)

        _tool.__name__ = skill_name
        _tool.__doc__ = (fn.__doc__ or skill_name).strip().splitlines()[0]
        return _tool

    tools = [
        _make_tool(name, fn) for name, fn in skills.items() if fn is not None
    ]

    try:
        agent = client.create_agent(
            name=spec.get("name", "agent"),
            instructions=system_prompt,
            tools=tools,
        )
        result = await agent.run(user_prompt)
        text = getattr(result, "text", None) or str(result)
    except Exception as exc:  # noqa: BLE001 — runtime failure → fall back
        raise MAFUnavailable(f"MAF agent run failed: {exc}") from exc

    parsed = _parse_json(text)
    # Best-effort token usage from the MAF result, if present.
    usage = getattr(result, "usage", None) or getattr(result, "usage_metadata", None)
    if usage:
        try:
            parsed.setdefault(
                "__usage__",
                usage if isinstance(usage, dict) else vars(usage),
            )
        except Exception:  # noqa: BLE001
            pass
    else:
        # Reuse the LangChain usage extractor in case the shape matches.
        parsed = _attach_usage(parsed, result)
    logger.info("[MAF] %s ran on Microsoft Agent Framework", spec.get("name", "agent"))
    return parsed
