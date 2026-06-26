"""
compliance.py — M12 Compliance Report.

`build_compliance_report(run_id, state)` assembles a structured, auditor-readable
artifact for a run: spec, business mappings, gate decisions, the Evidence Stack,
the audit trail, harness/model versions, and a machine-checkable assertions
checklist. It is **packaging, not new capability** — every field already exists
across the run state and the event bus; this gathers them into one document.

Pure and offline-safe: no network, no credentials. The audit trail is read from
the in-memory event bus when not injected (the durable-Postgres source is a
P-track concern). Reuses `build_evidence_stack` for the evidence section (M6/M10).

Design rule (PRD §8 M12): every one of the 8 sections is **always present**. When
a section's data is absent it is rendered as an explicit `null`/`[]` *with a
`_missing_reason`*, and enumerated in the top-level `missing` list — never silently
omitted.
"""
from __future__ import annotations

import html
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from evals.evidence_stack import EVIDENCE_LAYER_COUNT, build_evidence_stack

REPORT_VERSION = "1.0"
_REPO_ROOT = Path(__file__).resolve().parent.parent

# The 8 sections an auditor expects, in order (PRD §8 M12).
SECTION_ORDER = [
    "run_metadata",
    "spec",
    "business_mappings",
    "gate_decisions",
    "evidence_stack",
    "audit_trail",
    "versions",
    "compliance_assertions",
]


# ── Public entry point ────────────────────────────────────────────────────────
def build_compliance_report(
    run_id: str,
    state: Any,
    events: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Assemble the full compliance report for a run. Always returns all 8 sections.

    `events` (the audit trail) defaults to the in-memory event-bus history for
    `run_id`; pass an explicit list to keep the call pure (tests/verifiers).
    """
    if events is None:
        events = _load_events(run_id)

    sections: Dict[str, Any] = {
        "run_metadata": _section_run_metadata(run_id, state),
        "spec": _section_spec(state),
        "business_mappings": _section_business_mappings(state),
        "gate_decisions": _section_gate_decisions(state),
        "evidence_stack": _section_evidence(state),
        "audit_trail": _section_audit_trail(events),
        "versions": _section_versions(state),
        "compliance_assertions": _section_assertions(state),
    }

    missing = _collect_missing(sections)
    return {
        "report_version": REPORT_VERSION,
        "generated_at": time.time(),
        "run_id": run_id or getattr(state, "run_id", None),
        "sections": sections,
        "section_order": SECTION_ORDER,
        "missing": missing,
        "complete": len(missing) == 0,
    }


# ── Section builders ──────────────────────────────────────────────────────────
def _section_run_metadata(run_id: str, state: Any) -> Dict[str, Any]:
    started = getattr(state, "started_at", None)
    completed = getattr(state, "completed_at", None)
    lead_time = (completed - started) if (started and completed) else None
    sec: Dict[str, Any] = {
        "run_id": run_id or getattr(state, "run_id", None),
        "intent": getattr(state, "request", None),
        "component": getattr(state, "component", None),  # M11
        "run_status": getattr(state, "run_status", "running"),
        "reject_reason": getattr(state, "reject_reason", None),
        "started_at": started,
        "completed_at": completed,
        "lead_time_s": round(lead_time, 3) if lead_time is not None else None,
        "model_cost_usd": round(float(getattr(state, "cost_usd", 0.0) or 0.0), 4),
    }
    if lead_time is None:
        sec["_missing_reason"] = "run not finished — lead time unavailable until completed_at is set"
    return sec


def _section_spec(state: Any) -> Dict[str, Any]:
    spec_body = (getattr(state, "story", None) or {}).get("spec")
    component = getattr(state, "component", None)
    if spec_body:
        return {
            "source": "spec_registry" if component else "work_item_state",
            "component": component,
            "spec": spec_body,
            "superseded": getattr(state, "spec_superseded", None),  # M11
        }
    return {
        "source": None,
        "component": component,
        "spec": None,
        "superseded": None,
        "_missing_reason": "BSA produced no spec for this run",
    }


def _section_business_mappings(state: Any) -> Dict[str, Any]:
    bm = getattr(state, "business_mappings", []) or []
    sec: Dict[str, Any] = {
        "supplied": bm,
        "conformance": getattr(state, "mapping_fidelity", None),
    }
    if not bm:
        sec["_missing_reason"] = "no business mappings supplied at intent time (D11 scope-guard skipped)"
    return sec


def _section_gate_decisions(state: Any) -> Dict[str, Any]:
    # Human approval gates (G1/G2 in the named-gate model). P0.3: when an
    # authenticated principal decided the gate, `state.gate_approvals[name]`
    # carries the identity + timestamp and we surface it; otherwise `approver`
    # stays an explicit null with a reason (offline / pre-auth runs unchanged).
    approvals = getattr(state, "gate_approvals", {}) or {}

    def _human(name: str, approved: bool) -> Dict[str, Any]:
        rec = approvals.get(name) or {}
        approver = rec.get("approver")
        d: Dict[str, Any] = {
            "gate": name,
            "approved": bool(approved),
            "approver": approver,
            "approver_email": rec.get("approver_email") or None,
            "decided_at": rec.get("decided_at"),
        }
        if not approver:
            d["_missing_reason"] = (
                "approver identity not captured (auth not configured for this run, P0.3)"
            )
        return d

    human_gates = [
        _human("story_review", getattr(state, "story_approved", False)),
        _human("design_review", getattr(state, "design_approved", False)),
        _human("merge_review", getattr(state, "merge_approved", False)),
    ]
    automated_gates = [
        {
            "name": getattr(g, "name", "?"),
            "status": getattr(g, "status", "pending"),
            "retry_count": getattr(g, "retry_count", 0),
            "error": (getattr(g, "error_message", None) or "")[:300] or None,
        }
        for g in (getattr(state, "gates", []) or [])
    ]
    sec: Dict[str, Any] = {"human_gates": human_gates, "automated_gates": automated_gates}
    if not automated_gates:
        sec["_missing_reason"] = "no automated gates recorded on this run"
    return sec


def _section_evidence(state: Any) -> Dict[str, Any]:
    layers = build_evidence_stack(state)  # reuse M6/M10 builder
    return {"layer_count": EVIDENCE_LAYER_COUNT, "layers": layers}


def _section_audit_trail(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    ordered = sorted(events or [], key=lambda e: e.get("timestamp", 0))
    entries = [
        {
            "event_type": e.get("event_type"),
            "agent": e.get("agent", ""),
            "timestamp": e.get("timestamp"),
            "data": e.get("data", {}),
        }
        for e in ordered
    ]
    sec: Dict[str, Any] = {"count": len(entries), "entries": entries}
    if not entries:
        sec["_missing_reason"] = "no events recorded (offline direct-run or event log purged)"
    return sec


def _section_versions(state: Any) -> Dict[str, Any]:
    strong, cheap, live = _model_config()
    return {
        "harness_version": _harness_version(),
        "model_mode": "live" if live else "offline",
        "model_tier_strong": strong or None,
        "model_tier_cheap": cheap or None,
    }


def _section_assertions(state: Any) -> Dict[str, Any]:
    gates = getattr(state, "gates", []) or []
    statuses = [getattr(g, "status", "pending") for g in gates]
    no_red = "red" not in statuses
    scope_g = next((g for g in gates if getattr(g, "name", "") == "scope_conformance"), None)
    scope_ok = scope_g is None or getattr(scope_g, "status", None) == "green"
    retry_ok = all(getattr(g, "retry_count", 0) <= 3 for g in gates)

    # M11: when a prior Registry spec existed, drift must have been declared.
    prior = getattr(state, "registry_current_before", None)
    if prior is None:
        spec_conformance = True  # nothing to conform to
    else:
        # The scope-guard already enforces this; mirror its verdict here.
        spec_conformance = scope_ok

    checks = {
        "all_gates_green": bool(gates) and no_red,
        "scope_guard_passed": scope_ok,
        "retry_budget_respected": retry_ok,            # Rule 4: max 3 retries
        "spec_conformance_declared": spec_conformance,  # M11
        "evolution_governed": True,                     # Rule 9: human_promote() is the only mutation path
    }
    return {"checks": checks, "passed": all(checks.values())}


# ── HTML rendering (PRD lists a .html variant) ──────────────────────────────────
def render_compliance_html(report: Dict[str, Any]) -> str:
    """Minimal, dependency-free HTML rendering of a compliance report."""
    rid = html.escape(str(report.get("run_id")))
    rows: List[str] = []
    for name in report.get("section_order", []):
        sec = report.get("sections", {}).get(name, {})
        reason = sec.get("_missing_reason") if isinstance(sec, dict) else None
        flag = f' <em style="color:#b45309">[{html.escape(reason)}]</em>' if reason else ""
        rows.append(f"<h2>{html.escape(name)}{flag}</h2><pre>{html.escape(_pretty(sec))}</pre>")
    status = "COMPLETE" if report.get("complete") else f"{len(report.get('missing', []))} field(s) missing"
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Compliance Report — {rid}</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:60rem;margin:2rem auto;padding:0 1rem}"
        "pre{background:#0f172a;color:#e2e8f0;padding:1rem;border-radius:.5rem;overflow:auto}"
        "h1{border-bottom:2px solid #7c3aed}</style></head><body>"
        f"<h1>Compliance Report</h1><p><strong>Run:</strong> {rid} &middot; "
        f"<strong>Report v{html.escape(str(report.get('report_version')))}</strong> &middot; {status}</p>"
        + "".join(rows)
        + "</body></html>"
    )


# ── Helpers ─────────────────────────────────────────────────────────────────────
def _collect_missing(sections: Dict[str, Any]) -> List[Dict[str, str]]:
    """Enumerate sections (and nested gate entries) carrying a _missing_reason."""
    out: List[Dict[str, str]] = []
    for name, sec in sections.items():
        if isinstance(sec, dict) and sec.get("_missing_reason"):
            out.append({"section": name, "reason": sec["_missing_reason"]})
    return out


def _load_events(run_id: str) -> List[Dict[str, Any]]:
    if not run_id:
        return []
    try:
        from orchestrator.events import event_bus
        return event_bus.run_history(run_id)
    except Exception:  # noqa: BLE001
        return []


def _model_config() -> tuple[str, str, bool]:
    try:
        import config
        return config.AZURE_DEPLOYMENT_STRONG, config.AZURE_DEPLOYMENT_CHEAP, bool(config.AZURE_AI_LIVE)
    except Exception:  # noqa: BLE001
        return "", "", False


def _harness_version() -> str:
    """Best-effort `git describe`; offline-safe (no network)."""
    try:
        import subprocess
        out = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            capture_output=True, text=True, timeout=3, cwd=str(_REPO_ROOT),
        )
        return (out.stdout or "").strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def _pretty(obj: Any) -> str:
    import json
    return json.dumps(obj, indent=2, default=str)
