"""
emit_pdlc skill (M8 D33): write Continuum pipeline artifacts to .pdlc/ in a target app.

Thin agent-callable wrapper around orchestrator.pdlc.emit_pdlc_artifacts. Accepts
explicit fields rather than a state object so the LLM can supply them as tool args.
Offline-safe: delegates to the orchestrator module which uses only file I/O.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parent.parent.parent.parent


async def emit_pdlc(
    target_path: str,
    run_id: str = "",
    request: str = "",
    run_status: str = "done",
    cost_usd: float = 0.0,
    code: Optional[Dict[str, str]] = None,
    contract: Optional[str] = None,
    schema_sql: Optional[str] = None,
    mapping_fidelity: Optional[Dict[str, Any]] = None,
    business_mappings: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """
    Write Continuum pipeline artifacts to .pdlc/ in the target repo (M8 D33).

    Args:
        target_path:      Absolute path to the Layer 2 target application repo.
        run_id:           Continuum run identifier.
        request:          Original feature request text.
        run_status:       Pipeline lifecycle status (done/blocked/returned/failed).
        cost_usd:         Accumulated model cost for the run.
        code:             Generated code files {file_path: content}.
        contract:         OpenAPI contract YAML string from Architect.
        schema_sql:       SQL DDL string from Architect.
        mapping_fidelity: Scope-guard result dict (M7).
        business_mappings: Supplied code→label pairs (M7).

    Returns:
        {"pdlc_path": str, "files_written": int, "files": list[str]}
    """
    # Build a lightweight state-like object so we can reuse emit_pdlc_artifacts.
    class _FakeState:
        pass

    s = _FakeState()
    s.run_id = run_id
    s.request = request
    s.run_status = run_status
    s.cost_usd = cost_usd
    s.business_mappings = business_mappings or []
    s.mapping_fidelity = mapping_fidelity
    s.reject_reason = None
    s.code = code or {}
    s.contract = contract
    s.schema = schema_sql
    s.gates = []
    s.story_approved = False
    s.design_approved = False
    s.merge_approved = False

    spec = importlib.util.spec_from_file_location(
        "orchestrator_pdlc",
        _ROOT / "orchestrator" / "pdlc.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return await mod.emit_pdlc_artifacts(s, target_path)
