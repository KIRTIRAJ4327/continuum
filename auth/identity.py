"""
auth.identity — who is calling? (P0.3)

A `Principal` is the resolved identity behind a request: a user id, email,
display name, a set of `Role`s, and the `tenant_id` whose data they may touch.

`resolve_principal(token)` turns a bearer token into a Principal:

  • Auth NOT configured (`CONTINUUM_AUTH` unset/false) → always `DEV_PRINCIPAL`
    (ADMIN in the `default` tenant). This is what keeps every offline path and
    the header-less UI byte-identical to pre-P0.3 behaviour.

  • Auth configured + valid dev token → the Principal encoded in the token.
  • Auth configured + missing/invalid token → `AuthError` (→ HTTP 401).

Dev token format (offline-safe, no network, no signature check):

    cc.<base64url(json)>        json = {sub, email, name, roles[], tenant}

This is deliberately a *development* token so the RBAC/tenancy logic is fully
exercisable offline. Real JWT / Azure AD (Entra ID) validation — signature,
issuer, JWKS — is the live follow-up that plugs in behind the same
`resolve_principal` seam; nothing else in the codebase needs to change.
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .errors import AuthError
from .rbac import Role

DEFAULT_TENANT = "default"
_DEV_TOKEN_PREFIX = "cc."


@dataclass
class Principal:
    """A resolved caller identity."""

    user_id: str
    email: str = ""
    display_name: str = ""
    roles: List[Role] = field(default_factory=list)
    tenant_id: str = DEFAULT_TENANT
    auth_mode: str = "offline-dev"  # "offline-dev" | "dev-token" | "jwt"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "email": self.email,
            "display_name": self.display_name,
            "roles": [r.value for r in self.roles],
            "tenant_id": self.tenant_id,
            "auth_mode": self.auth_mode,
        }


# The identity used on every offline / unauthenticated path. ADMIN so the verify
# suite and header-less UI keep full access exactly as before P0.3.
DEV_PRINCIPAL = Principal(
    user_id="dev-local",
    email="dev@continuum.local",
    display_name="Local Dev",
    roles=[Role.ADMIN],
    tenant_id=DEFAULT_TENANT,
    auth_mode="offline-dev",
)


def auth_configured() -> bool:
    """True when CONTINUUM_AUTH is a truthy string (1/true/yes/on)."""
    return os.getenv("CONTINUUM_AUTH", "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_roles(raw: Any) -> List[Role]:
    roles: List[Role] = []
    for r in raw or []:
        try:
            roles.append(Role(str(r).lower()))
        except ValueError:
            continue
    return roles


def _principal_from_claims(claims: Dict[str, Any], auth_mode: str) -> Principal:
    """Build a Principal from a claims dict (dev token or, later, a JWT)."""
    user_id = str(claims.get("sub") or claims.get("user_id") or "").strip()
    if not user_id:
        raise AuthError("token missing subject (sub)")
    return Principal(
        user_id=user_id,
        email=str(claims.get("email") or ""),
        display_name=str(claims.get("name") or claims.get("display_name") or user_id),
        roles=_parse_roles(claims.get("roles")),
        tenant_id=str(claims.get("tenant") or claims.get("tenant_id") or DEFAULT_TENANT),
        auth_mode=auth_mode,
    )


def encode_dev_token(
    sub: str,
    *,
    roles: Optional[List[str]] = None,
    tenant: str = DEFAULT_TENANT,
    email: str = "",
    name: str = "",
) -> str:
    """Encode a dev token (`cc.<base64url(json)>`) — used by tests and tooling."""
    payload = {
        "sub": sub,
        "email": email,
        "name": name or sub,
        "roles": roles or [],
        "tenant": tenant,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    b64 = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return _DEV_TOKEN_PREFIX + b64


def _decode_dev_token(token: str) -> Dict[str, Any]:
    body = token[len(_DEV_TOKEN_PREFIX):]
    pad = "=" * (-len(body) % 4)
    try:
        raw = base64.urlsafe_b64decode(body + pad)
        claims = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise AuthError(f"malformed dev token: {exc}") from exc
    if not isinstance(claims, dict):
        raise AuthError("dev token payload is not an object")
    return claims


def _strip_bearer(token: Optional[str]) -> str:
    """Accept either a raw token or an 'Authorization: Bearer <token>' value."""
    if not token:
        return ""
    t = token.strip()
    if t.lower().startswith("bearer "):
        t = t[7:].strip()
    return t


def resolve_principal(
    token: Optional[str],
    *,
    configured: Optional[bool] = None,
) -> Principal:
    """
    Resolve a caller identity from a bearer token.

    `configured` overrides `auth_configured()` (tests pass it explicitly). When
    auth is off, returns `DEV_PRINCIPAL` regardless of token. When on, requires a
    valid token and raises `AuthError` otherwise.
    """
    if configured is None:
        configured = auth_configured()

    if not configured:
        return DEV_PRINCIPAL

    raw = _strip_bearer(token)
    if not raw:
        raise AuthError("authentication required: no bearer token")

    if raw.startswith(_DEV_TOKEN_PREFIX):
        return _principal_from_claims(_decode_dev_token(raw), auth_mode="dev-token")

    # A non-dev token under configured auth means a real JWT is expected — that
    # validation path is the live follow-up; reject clearly rather than guess.
    raise AuthError(
        "unrecognised token format (JWT validation is a live-only follow-up; "
        "use a dev token 'cc.<base64url(json)>' offline)"
    )
