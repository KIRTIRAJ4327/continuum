"""
C3: notification channels — alert a reviewer when a gate needs attention.

Offline-safe by construction: a notifier only does network IO when its webhook
URL env var is set, and `get_notifiers()` returns an empty list when none are
configured — so the verify suite and any offline run send nothing and never
import an HTTP client. Uses urllib (stdlib) so there is no hard aiohttp dep.
"""
from __future__ import annotations

import json
import logging
import os
from typing import List, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class NotificationChannel(Protocol):
    async def send(self, message: str, run_id: str, gate: str) -> None: ...


def _post_json(url: str, payload: dict) -> None:
    """Best-effort synchronous JSON POST (stdlib). Never raises."""
    try:
        import urllib.request

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        urllib.request.urlopen(req, timeout=5)  # noqa: S310 — operator-configured URL
    except Exception as exc:  # noqa: BLE001 — a notification must never break a run
        logger.warning("[notify] POST to %s failed: %s", url[:40], exc)


def _review_link(run_id: str) -> str:
    base = os.getenv("CONTINUUM_BASE_URL", "").rstrip("/")
    return f"{base}/run/{run_id}" if base else f"run/{run_id}"


class SlackNotifier:
    """POSTs a Slack message when CONTINUUM_SLACK_WEBHOOK_URL is set."""

    async def send(self, message: str, run_id: str, gate: str) -> None:
        url = os.getenv("CONTINUUM_SLACK_WEBHOOK_URL")
        if not url:
            return
        _post_json(url, {
            "text": (
                f":bell: *Gate review needed* — `{gate}`\n"
                f"Run: `{run_id}`\n{message}\n"
                f"Review: {_review_link(run_id)}"
            )
        })


class TeamsNotifier:
    """POSTs an Adaptive-Card-ish message when CONTINUUM_TEAMS_WEBHOOK_URL is set."""

    async def send(self, message: str, run_id: str, gate: str) -> None:
        url = os.getenv("CONTINUUM_TEAMS_WEBHOOK_URL")
        if not url:
            return
        _post_json(url, {
            "title": f"Gate review needed — {gate}",
            "text": f"Run {run_id}: {message}  \n[Review]({_review_link(run_id)})",
        })


def get_notifiers() -> List[NotificationChannel]:
    """
    Return the notifiers configured via env. Empty list (no-op) when none set —
    this is the offline default, so a run with no webhook env vars sends nothing.
    """
    notifiers: List[NotificationChannel] = []
    if os.getenv("CONTINUUM_SLACK_WEBHOOK_URL"):
        notifiers.append(SlackNotifier())
    if os.getenv("CONTINUUM_TEAMS_WEBHOOK_URL"):
        notifiers.append(TeamsNotifier())
    return notifiers


async def notify_gate_pending(run_id: str, gate: str, message: str = "") -> None:
    """Fan a gate-pending alert out to all configured channels (best-effort)."""
    msg = message or f"Gate '{gate}' is awaiting review."
    for n in get_notifiers():
        try:
            await n.send(msg, run_id, gate)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[notify] channel failed: %s", exc)
