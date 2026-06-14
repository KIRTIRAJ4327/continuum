"""
write_spec_registry skill (M11) — persist a run's spec into the Spec Registry as a
new version, recording a SUPERSEDES relationship when it materially differs from
the prior current spec for the same component.

Distinct from the `write_spec` skill, which only *formats* a spec document into
state — this one *persists* it (append-only, versioned). Offline-safe: with no
driver, writes to the process-level in-memory store. Never raises.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


async def write_spec_registry(
    component: str,
    spec_body: Dict[str, Any],
    request: str = "",
    run_id: str = "",
    prior_spec: Optional[Dict[str, Any]] = None,
    supersedes_reason: str = "",
    neo4j_driver: Any = None,
) -> Dict[str, Any]:
    """
    Persist `spec_body` as the current Registry spec for `component`.

    If `prior_spec` exists and the new body materially differs from it
    (structural diff via spec_registry.specs_match), record a supersession and
    return its details under `superseded`.

    Returns {spec_id, component, version, superseded, stub}.
    """
    from graph_db import spec_registry

    if not component:
        return {"spec_id": "", "component": component, "version": 0, "superseded": None, "stub": True}

    try:
        res = await spec_registry.write_spec(
            component, spec_body, request_text=request, run_id=run_id, neo4j_driver=neo4j_driver,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[write_spec_registry] write failed: %s", exc)
        return {"spec_id": "", "component": component, "version": 0, "superseded": None, "stub": True}

    superseded = None
    prior_id = (prior_spec or {}).get("id")
    if prior_id:
        prior_body = (prior_spec or {}).get("body", {})
        if not spec_registry.specs_match(spec_body, prior_body):
            reason = supersedes_reason or f"spec updated for {component}"
            try:
                ok = await spec_registry.mark_superseded(
                    prior_id, res["spec_id"], reason, neo4j_driver=neo4j_driver,
                )
                if ok:
                    superseded = {"old_id": prior_id, "reason": reason}
            except Exception as exc:  # noqa: BLE001
                logger.warning("[write_spec_registry] mark_superseded failed: %s", exc)

    res["superseded"] = superseded
    logger.info(
        "[write_spec_registry] component=%s v%s superseded=%s",
        component, res.get("version"), bool(superseded),
    )
    return res
