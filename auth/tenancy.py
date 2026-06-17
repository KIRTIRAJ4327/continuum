"""
auth.tenancy — whose data is it? (P0.3)

Multi-tenant isolation helpers. The load-bearing idea is `tenant_key()`: a
deterministic namespacing of any storage key by tenant, with the **`default`
tenant returning the bare key**. That default-guard is what keeps M11's Spec
Registry component keys (and every other offline path) byte-identical to
pre-P0.3 — tenancy only changes keys once a real, non-default tenant is in play.

Visibility:
  • A principal sees runs in **their own** tenant.
  • An ADMIN sees **all** tenants (cross-tenant operability for platform ops).
"""
from __future__ import annotations

from typing import Any, Dict

from .identity import DEFAULT_TENANT
from .rbac import Role


def is_default_tenant(tenant_id: Any) -> bool:
    """True for the unset / 'default' tenant (the offline, single-tenant world)."""
    return not tenant_id or str(tenant_id) == DEFAULT_TENANT


def tenant_key(tenant_id: Any, base_key: str) -> str:
    """
    Namespace `base_key` under `tenant_id`.

    Default tenant → bare key (no change vs. pre-P0.3). Any other tenant →
    ``"<tenant_id>:<base_key>"``. Single source of truth for per-tenant keying
    of the spec registry, run store, and Neo4j subgraph labels.
    """
    if is_default_tenant(tenant_id):
        return base_key
    return f"{tenant_id}:{base_key}"


def _has_role(principal: Any, role: Role) -> bool:
    for r in getattr(principal, "roles", []) or []:
        if r == role or str(r) == role.value:
            return True
    return False


def can_access_tenant(principal: Any, tenant_id: Any) -> bool:
    """ADMIN may access any tenant; everyone else only their own."""
    if _has_role(principal, Role.ADMIN):
        return True
    own = getattr(principal, "tenant_id", DEFAULT_TENANT)
    return str(own) == str(tenant_id or DEFAULT_TENANT)


def visible_runs(principal: Any, runs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Filter a {run_id: state} mapping to the runs this principal may see.

    A run's tenant is read from `state.tenant_id` (default 'default'). ADMINs see
    everything; others see only their own tenant's runs.
    """
    if _has_role(principal, Role.ADMIN):
        return dict(runs)
    own = str(getattr(principal, "tenant_id", DEFAULT_TENANT))
    return {
        rid: st
        for rid, st in runs.items()
        if str(getattr(st, "tenant_id", DEFAULT_TENANT)) == own
    }
