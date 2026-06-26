#!/usr/bin/env python
"""
verify_c3_extensibility.py — C3 Extensibility Sprint (3/3).

Verifies the integration layer that lets external systems drive Continuum and
lets new apps onboard via config, not code — all offline-safe.

  A. parse_mapping_tags() extracts business mappings from an ADO tag string,
     and should_trigger()/extract_intent() classify a sample webhook payload.
  B. _load_repo_config() reads a .pdlc/config.yml fixture (and falls back to
     the Python defaults when the file is absent).
  C. get_notifiers() returns [] when no webhook env vars are set (offline-safe,
     no network, no aiohttp import).

Usage:
  python scripts/verify_c3_extensibility.py
Expected output:
  3/3 checks passed
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.webhooks import parse_mapping_tags, extract_intent, should_trigger  # noqa: E402
from integrations.notifications import get_notifiers  # noqa: E402
from orchestrator.agent_runner import _load_repo_config  # noqa: E402

CHECKS: list = []


def _check(name: str, passed: bool, detail: str = "") -> None:
    status = "PASS" if passed else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"  [{status}] {name}{suffix}")
    CHECKS.append((name, passed))


def main() -> int:
    print()
    print("C3 Extensibility verification (3/3)")
    print("=" * 40)

    # ── Case A: ADO webhook parsing ──────────────────────────────────────── #
    print("\nCase A: ADO webhook parse + trigger classification")
    mappings = parse_mapping_tags("ready; mapping:BR=Branch,WEB=Web; ui")
    payload = {
        "eventType": "workitem.created",
        "resource": {"fields": {
            "System.WorkItemType": "User Story",
            "System.Title": "Add channel name to Product Selection",
            "System.Description": "<div>Show the channel name field.</div>",
            "System.Tags": "mapping:BR=Branch,WEB=Web",
        }},
    }
    intent = extract_intent(payload)
    ignored = should_trigger({"eventType": "workitem.deleted", "resource": {}})
    _check(
        "mappings parsed, intent built, trigger classified",
        mappings == [{"code": "BR", "label": "Branch"}, {"code": "WEB", "label": "Web"}]
        and "channel name" in intent.lower()
        and should_trigger(payload) is True
        and ignored is False,
        f"mappings={len(mappings)} intent={intent[:40]!r}",
    )

    # ── Case B: .pdlc/config.yml loader ──────────────────────────────────── #
    print("\nCase B: _load_repo_config reads .pdlc/config.yml (else defaults)")
    defaults = _load_repo_config("/nonexistent/path")
    have_yaml = False
    try:
        import yaml  # noqa: F401
        have_yaml = True
    except Exception:  # noqa: BLE001
        have_yaml = False

    cfg_ok = defaults.get("test_cmd") == "pytest -q" and defaults.get("stack") == "python"
    if have_yaml:
        with tempfile.TemporaryDirectory() as d:
            pdlc = Path(d) / ".pdlc"
            pdlc.mkdir()
            (pdlc / "config.yml").write_text(
                "app_name: Retail Banking App\n"
                "stack: java-spring-angular\n"
                "lint_cmd: mvn checkstyle:check\n"
                "test_cmd: mvn test -q\n",
                encoding="utf-8",
            )
            loaded = _load_repo_config(d)
            cfg_ok = cfg_ok and loaded["stack"] == "java-spring-angular" and loaded["test_cmd"] == "mvn test -q"
        detail = "defaults + java fixture overrides"
    else:
        detail = "defaults only (PyYAML absent)"
    _check("config loader: defaults + fixture override", cfg_ok, detail)

    # ── Case C: notifiers are offline-safe ───────────────────────────────── #
    print("\nCase C: get_notifiers() is empty without webhook env vars")
    for var in ("CONTINUUM_SLACK_WEBHOOK_URL", "CONTINUUM_TEAMS_WEBHOOK_URL"):
        os.environ.pop(var, None)
    notifiers = get_notifiers()
    _check("no webhook env → [] (no-op)", notifiers == [], f"n={len(notifiers)}")

    # ── Summary ──────────────────────────────────────────────────────────── #
    print()
    print("=" * 40)
    passed = sum(1 for _, ok in CHECKS if ok)
    total = len(CHECKS)
    print(f"{passed}/{total} checks passed")
    print()
    if passed == total:
        print("C3 Extensibility: OK")
    else:
        print("FAIL — fix the checks above")
        for name, ok in CHECKS:
            if not ok:
                print(f"  FAILED: {name}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
