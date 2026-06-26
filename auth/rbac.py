"""
auth.rbac — role-based access control (P0.3).

Three roles, a fixed permission matrix, and two pure predicates: `can()` and
`require()`. No imports from `identity` (identity imports `Role` from here), so
the dependency runs one way: identity → rbac. `can()` duck-types its principal
(reads `.roles`) so it never needs the `Principal` type.

Role model (PRD P0.3):
  DEV       — submit feature requests, view runs.
  REVIEWER  — view runs, approve/reject human gates, read compliance reports.
  ADMIN     — everything, plus promote Evolution proposals and manage tenants.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Set

from .errors import PermissionDenied


class Role(str, Enum):
    DEV = "dev"
    REVIEWER = "reviewer"
    ADMIN = "admin"


class Permission(str, Enum):
    SUBMIT_RUN = "submit_run"
    VIEW_RUN = "view_run"
    APPROVE_GATE = "approve_gate"
    REJECT_GATE = "reject_gate"
    VIEW_COMPLIANCE = "view_compliance"
    PROMOTE_EVOLUTION = "promote_evolution"
    MANAGE_TENANT = "manage_tenant"


# The permission matrix. ADMIN is computed as the union of all permissions so a
# newly-added Permission is automatically granted to admins (and must be
# explicitly added to DEV/REVIEWER, never silently leaked).
_ALL: Set[Permission] = set(Permission)

ROLE_PERMISSIONS: Dict[Role, Set[Permission]] = {
    Role.DEV: {
        Permission.SUBMIT_RUN,
        Permission.VIEW_RUN,
    },
    Role.REVIEWER: {
        Permission.VIEW_RUN,
        Permission.APPROVE_GATE,
        Permission.REJECT_GATE,
        Permission.VIEW_COMPLIANCE,
    },
    Role.ADMIN: set(_ALL),
}


def _roles_of(principal: Any) -> Set[Role]:
    """Duck-type the principal's roles into a set of Role (unknowns dropped)."""
    out: Set[Role] = set()
    for r in getattr(principal, "roles", []) or []:
        if isinstance(r, Role):
            out.add(r)
        else:
            try:
                out.add(Role(str(r)))
            except ValueError:
                continue
    return out


def can(principal: Any, permission: Permission) -> bool:
    """True iff any of the principal's roles grants `permission`. Pure predicate."""
    return any(
        permission in ROLE_PERMISSIONS.get(role, set())
        for role in _roles_of(principal)
    )


def require(principal: Any, permission: Permission) -> None:
    """Raise `PermissionDenied` unless `can(principal, permission)`."""
    if not can(principal, permission):
        who = getattr(principal, "user_id", None) or "anonymous"
        raise PermissionDenied(
            f"{who} lacks permission '{permission.value}'",
            permission=permission.value,
        )
