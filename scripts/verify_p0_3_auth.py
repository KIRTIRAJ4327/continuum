#!/usr/bin/env python
"""
verify_p0_3_auth.py — P0.3 Auth + Tenancy.

Identity, RBAC, and multi-tenant isolation — the gate for any real-client use.
Every check is pure / offline (the auth package imports no network, no DB):

  1. Offline default: auth not configured → resolve_principal(None) is the ADMIN
     DEV_PRINCIPAL in the 'default' tenant (so M0–M13 + UI are byte-unchanged).
  2. RBAC matrix: DEV may SUBMIT_RUN not APPROVE_GATE; REVIEWER may APPROVE_GATE
     not SUBMIT_RUN; ADMIN may do both + PROMOTE_EVOLUTION. require() denies.
  3. Dev-token identity under configured auth: roles + tenant parsed; a missing
     token raises AuthError (→ 401).
  4. Tenant isolation: tenant_key default→bare (M11 keys unchanged), other→prefixed;
     visible_runs filters to a principal's tenant; ADMIN sees all; can_access_tenant.
  5. Approver capture → M12: _record_approval stamps identity onto state, and the
     compliance report shows the approver (no longer the P0.3 null); a run with no
     captured approval still reports the explicit null + reason (backward compatible).
  6. Offline pipeline unchanged: a full _execute_pipeline run defaults tenant_id to
     'default' and records no spurious approvals (M0 behaviour intact under P0.3).

Usage:
  python scripts/verify_p0_3_auth.py
Expected output:
  6/6 checks passed
"""
from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.compliance import build_compliance_report  # noqa: E402
from api.main import _execute_pipeline, _record_approval  # noqa: E402
from auth import (  # noqa: E402
    DEV_PRINCIPAL,
    Permission,
    Role,
    can,
    can_access_tenant,
    encode_dev_token,
    require,
    resolve_principal,
    tenant_key,
    visible_runs,
)
from auth.errors import AuthError, PermissionDenied  # noqa: E402
from orchestrator.state import ContinuumState  # noqa: E402

CHECKS: list = []


def _check(name: str, passed: bool, detail: str = "") -> None:
    icon = "PASS" if passed else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"  [{icon}] {name}{suffix}")
    CHECKS.append((name, passed, detail))


def _check1_offline_default() -> None:
    p = resolve_principal(None, configured=False)
    ok = (
        p is DEV_PRINCIPAL
        and Role.ADMIN in p.roles
        and p.tenant_id == "default"
        and can(p, Permission.SUBMIT_RUN)
        and can(p, Permission.APPROVE_GATE)
        and can(p, Permission.VIEW_COMPLIANCE)
    )
    _check(
        "offline default → ADMIN DEV_PRINCIPAL, default tenant, all perms",
        ok,
        f"roles={[r.value for r in p.roles]} tenant={p.tenant_id}",
    )


def _check2_rbac_matrix() -> None:
    dev = resolve_principal(encode_dev_token("d", roles=["dev"]), configured=True)
    rev = resolve_principal(encode_dev_token("r", roles=["reviewer"]), configured=True)
    adm = resolve_principal(encode_dev_token("a", roles=["admin"]), configured=True)

    dev_ok = can(dev, Permission.SUBMIT_RUN) and not can(dev, Permission.APPROVE_GATE)
    rev_ok = can(rev, Permission.APPROVE_GATE) and not can(rev, Permission.SUBMIT_RUN)
    adm_ok = can(adm, Permission.SUBMIT_RUN) and can(adm, Permission.PROMOTE_EVOLUTION)

    # require() raises for a disallowed action
    denied = False
    try:
        require(dev, Permission.APPROVE_GATE)
    except PermissionDenied:
        denied = True

    _check(
        "RBAC matrix: dev/reviewer/admin permissions + require() denies",
        dev_ok and rev_ok and adm_ok and denied,
        f"dev={dev_ok} reviewer={rev_ok} admin={adm_ok} denied={denied}",
    )


def _check3_dev_token_identity() -> None:
    tok = encode_dev_token("alice", roles=["reviewer"], tenant="acme", email="a@acme.io")
    p = resolve_principal("Bearer " + tok, configured=True)
    parsed_ok = (
        p.user_id == "alice"
        and p.tenant_id == "acme"
        and p.email == "a@acme.io"
        and Role.REVIEWER in p.roles
        and p.auth_mode == "dev-token"
    )
    # configured + no token → AuthError
    raised = False
    try:
        resolve_principal(None, configured=True)
    except AuthError:
        raised = True
    _check(
        "dev-token parsed under configured auth; missing token → AuthError",
        parsed_ok and raised,
        f"parsed={parsed_ok} no_token_401={raised}",
    )


def _check4_tenant_isolation() -> None:
    # tenant_key: default tenant leaves M11 keys untouched
    key_ok = tenant_key("default", "tasks-crud") == "tasks-crud" and \
        tenant_key("acme", "tasks-crud") == "acme:tasks-crud"

    acme_dev = resolve_principal(encode_dev_token("d", roles=["dev"], tenant="acme"), configured=True)
    beta_dev = resolve_principal(encode_dev_token("e", roles=["dev"], tenant="beta"), configured=True)

    runs = {
        "r1": ContinuumState(request="x", run_id="r1", tenant_id="acme"),
        "r2": ContinuumState(request="y", run_id="r2", tenant_id="beta"),
    }
    acme_view = visible_runs(acme_dev, runs)
    admin_view = visible_runs(DEV_PRINCIPAL, runs)

    isolation_ok = (
        set(acme_view) == {"r1"}                          # acme dev sees only acme
        and set(admin_view) == {"r1", "r2"}               # admin sees all
        and can_access_tenant(acme_dev, "acme")
        and not can_access_tenant(acme_dev, "beta")
        and not can_access_tenant(beta_dev, "acme")
    )
    _check(
        "tenant isolation: keys, visible_runs filter, cross-tenant denied, admin override",
        key_ok and isolation_ok,
        f"key_ok={key_ok} acme_view={sorted(acme_view)} admin_view={sorted(admin_view)}",
    )


def _check5_approver_capture_to_m12() -> None:
    reviewer = resolve_principal(
        encode_dev_token("bob", roles=["reviewer"], tenant="acme", email="bob@acme.io"),
        configured=True,
    )
    # A run with an approval captured
    st = ContinuumState(request="Build it", run_id="rc1", tenant_id="acme")
    st.story_approved = True
    _record_approval(st, "story_review", reviewer, approved=True)

    report = build_compliance_report("rc1", st, events=[])
    human = report["sections"]["gate_decisions"]["human_gates"]
    story = next(g for g in human if g["gate"] == "story_review")
    design = next(g for g in human if g["gate"] == "design_review")

    captured_ok = (
        story["approver"] == "bob"
        and story["approver_email"] == "bob@acme.io"
        and story.get("decided_at") is not None
        and "_missing_reason" not in story          # identity present → no missing flag
    )
    # Backward-compat: a gate with no captured approval still reports null + reason
    fallback_ok = (
        design["approver"] is None
        and "_missing_reason" in design
    )
    _check(
        "approver identity flows into M12 (no longer null); unrecorded gate keeps null+reason",
        captured_ok and fallback_ok,
        f"story_approver={story['approver']} design_null={design['approver'] is None}",
    )


async def _check6_offline_pipeline_unchanged() -> None:
    st = ContinuumState(request="Build a default-tenant widget", run_id="")
    await _execute_pipeline(st, run_id="")
    ok = (
        st.tenant_id == "default"
        and st.gate_approvals == {}
        and st.completed_at is not None
    )
    _check(
        "offline pipeline: tenant_id='default', no spurious approvals (M0 intact)",
        ok,
        f"tenant={st.tenant_id} approvals={st.gate_approvals} done={st.completed_at is not None}",
    )


async def main() -> int:
    print("P0.3 Auth + Tenancy verification")
    print("=" * 50)

    _check1_offline_default()
    _check2_rbac_matrix()
    _check3_dev_token_identity()
    _check4_tenant_isolation()
    _check5_approver_capture_to_m12()
    await _check6_offline_pipeline_unchanged()

    print()
    passed_n = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    print(f"{passed_n}/{total} checks passed")
    return 0 if passed_n == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
