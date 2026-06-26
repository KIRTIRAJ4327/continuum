"""
C4: optional Langfuse tracing — real traces + an Evidence-Stack score per run.

Strictly opt-in and offline-safe: every function is a no-op unless
LANGFUSE_SECRET_KEY + LANGFUSE_PUBLIC_KEY + LANGFUSE_HOST are all set AND the
`langfuse` SDK is importable. Any Langfuse error is swallowed — telemetry can
never break a run, and the offline path never imports the SDK.

Langfuse is MIT-licensed and self-hostable (Canada-resident), which is why it is
the default observability sink over a proprietary, Enterprise-self-host option.
See docs/LANGFUSE_SETUP.md.
"""
from __future__ import annotations

import logging
import os
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

_client: Any = None
_checked = False


def _enabled() -> bool:
    return bool(
        os.getenv("LANGFUSE_SECRET_KEY")
        and os.getenv("LANGFUSE_PUBLIC_KEY")
        and os.getenv("LANGFUSE_HOST")
    )


def _langfuse() -> Optional[Any]:
    """Lazily resolve a Langfuse client; cache the (possibly None) result."""
    global _client, _checked
    if _checked:
        return _client
    _checked = True
    if not _enabled():
        _client = None
        return None
    try:
        from langfuse import Langfuse  # type: ignore[import]

        _client = Langfuse(
            secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
            public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
            host=os.getenv("LANGFUSE_HOST"),
        )
    except Exception as exc:  # noqa: BLE001 — SDK absent or misconfigured → no-op
        logger.debug("[langfuse] disabled (%s)", exc)
        _client = None
    return _client


def trace_run(run_id: str, request: str, tenant_id: str = "") -> None:
    """Open a trace for a run. No-op unless Langfuse is configured."""
    client = _langfuse()
    if client is None:
        return
    try:
        client.trace(
            name="continuum_run",
            id=run_id,
            input=request,
            metadata={"tenant_id": tenant_id},
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[langfuse] trace_run failed: %s", exc)


def score_run(run_id: str, evidence_layers: List[dict]) -> None:
    """
    Record the Evidence-Stack pass rate as a Langfuse score for a run.
    No-op unless Langfuse is configured.
    """
    client = _langfuse()
    if client is None or not evidence_layers:
        return
    try:
        passed = sum(1 for l in evidence_layers if l.get("status") == "pass")
        client.score(
            trace_id=run_id,
            name="evidence_stack",
            value=passed / len(evidence_layers),
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[langfuse] score_run failed: %s", exc)


def _reset_cache() -> None:
    """Test hook: clear the cached client so env changes take effect."""
    global _client, _checked
    _client = None
    _checked = False
