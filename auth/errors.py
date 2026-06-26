"""
auth.errors — exception types for the auth layer.

Kept separate so `identity`, `rbac`, and `tenancy` can all raise/catch them
without importing each other. `api/main.py` maps these onto HTTP status codes
(401 for `AuthError`, 403 for `PermissionDenied`).
"""
from __future__ import annotations


class AuthError(Exception):
    """Authentication failed — no/invalid credentials. Maps to HTTP 401."""

    status_code = 401


class PermissionDenied(Exception):
    """Authenticated but not authorised for this action. Maps to HTTP 403."""

    status_code = 403

    def __init__(self, message: str, *, permission: str = "", tenant: str = "") -> None:
        super().__init__(message)
        self.permission = permission
        self.tenant = tenant
