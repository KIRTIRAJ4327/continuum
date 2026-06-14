"""
query_spec_registry skill (M11) — retrieve the current Registry spec + version
history for a component, so the BSA can ground a new run on past design decisions
and conform to (or explicitly supersede) what already exists.

Offline-safe: with no driver, reads from the process-level in-memory store. Never
raises — returns {found: False} when there is nothing for the component yet.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


async def query_spec_registry(component: str, neo4j_driver: Any = None) -> Dict[str, Any]:
    """
    Return {component, current, history, found} for `component`.

    `current` is the latest non-superseded spec (or None); `history` is the full
    version chain oldest-first; `found` is True when a current spec exists.
    """
    from graph_db import spec_registry

    if not component:
        return {"component": component, "current": None, "history": [], "found": False}

    try:
        current = await spec_registry.get_current_spec(component, neo4j_driver=neo4j_driver)
        history = await spec_registry.get_spec_history(component, neo4j_driver=neo4j_driver)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[query_spec_registry] failed: %s", exc)
        return {"component": component, "current": None, "history": [], "found": False}

    logger.info(
        "[query_spec_registry] component=%s found=%s versions=%d",
        component, current is not None, len(history),
    )
    return {
        "component": component,
        "current": current,
        "history": history,
        "found": current is not None,
    }
