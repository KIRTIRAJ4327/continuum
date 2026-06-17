"""
auth — Identity, RBAC, and multi-tenancy for Continuum (P0.3).

This package is a leaf: it imports nothing from `orchestrator`, `api`, or
`graph_db`, so it can be used anywhere without circular imports.

Three concerns, three modules:

  identity.py — who is the caller?  `Principal` + `resolve_principal(token)`.
  rbac.py     — what may they do?   `Role`/`Permission` + `can()` / `require()`.
  tenancy.py  — whose data is it?   `tenant_key()` + run/spec scoping helpers.

Offline-safe invariant: when auth is **not** configured (`CONTINUUM_AUTH`
unset/false), `resolve_principal(None)` returns the module-level `DEV_PRINCIPAL`
— an ADMIN in the `default` tenant — so the verify suite, the existing UI (which
sends no `Authorization` header), and every offline path behave exactly as they
did before P0.3. Enforcement only bites once auth is switched on.
"""
from __future__ import annotations

from .errors import AuthError, PermissionDenied
from .identity import (
    DEFAULT_TENANT,
    DEV_PRINCIPAL,
    Principal,
    auth_configured,
    encode_dev_token,
    resolve_principal,
)
from .rbac import Permission, Role, ROLE_PERMISSIONS, can, require
from .tenancy import (
    can_access_tenant,
    is_default_tenant,
    tenant_key,
    visible_runs,
)

__all__ = [
    "AuthError",
    "PermissionDenied",
    "DEFAULT_TENANT",
    "DEV_PRINCIPAL",
    "Principal",
    "auth_configured",
    "encode_dev_token",
    "resolve_principal",
    "Permission",
    "Role",
    "ROLE_PERMISSIONS",
    "can",
    "require",
    "can_access_tenant",
    "is_default_tenant",
    "tenant_key",
    "visible_runs",
]
