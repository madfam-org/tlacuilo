"""Janua RS256 verification (JWKS), the only identity tlacuilo knows.

Callers are service clients (dhanam, karafiel) holding the role `tlacuilo:extract`
granted through Janua app roles; the org claim scopes job visibility. No sessions, no
users, no API keys of our own — the same posture as karafiel/core/authentication.py."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import jwt
from fastapi import HTTPException, Request

from .settings import get_settings

log = logging.getLogger(__name__)
_jwks_client: jwt.PyJWKClient | None = None


def signing_key_for(token: str):
    """Resolve the RS256 public key for a token via Janua's JWKS (cached one hour).
    Tests monkeypatch this function; nothing else in the module knows about keys."""
    global _jwks_client
    if _jwks_client is None:
        base = get_settings().janua_base_url.rstrip("/")
        _jwks_client = jwt.PyJWKClient(f"{base}/.well-known/jwks.json", cache_keys=True, lifespan=3600)
    return _jwks_client.get_signing_key_from_jwt(token).key


@dataclass(frozen=True)
class Principal:
    sub: str
    org_id: str | None
    roles: frozenset[str] = field(default_factory=frozenset)


def _roles(claims: dict) -> frozenset[str]:
    roles: set[str] = set()
    for key in ("roles", "app_roles", "permissions"):
        value = claims.get(key)
        if isinstance(value, list):
            roles.update(str(v) for v in value)
    for key in ("scope", "scp"):
        value = claims.get(key)
        if isinstance(value, str):
            roles.update(value.split())
    return frozenset(roles)


def verify_token(token: str) -> Principal:
    s = get_settings()
    key = signing_key_for(token)
    claims = jwt.decode(
        token,
        key,
        algorithms=["RS256"],
        audience=s.janua_audience,
        issuer=s.janua_base_url,
        options={"require": ["exp", "iat", "sub"]},
    )
    org = claims.get("org_id") or claims.get("organization_id") or claims.get("tenant_org") or claims.get("org")
    return Principal(sub=str(claims["sub"]), org_id=str(org) if org else None, roles=_roles(claims))


async def require_principal(request: Request) -> Principal:
    s = get_settings()
    if s.auth_disabled:
        return Principal(
            sub="local", org_id=request.headers.get("x-debug-org", "local"), roles=frozenset({s.required_role})
        )
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail={"error": "missing_bearer"})
    token = header[7:].strip()
    try:
        principal = verify_token(token)
    except jwt.PyJWTError as exc:
        log.info("token rejected: %s", type(exc).__name__)
        raise HTTPException(status_code=401, detail={"error": "invalid_token", "reason": type(exc).__name__}) from None
    if s.required_role not in principal.roles and "tlacuilo:admin" not in principal.roles:
        raise HTTPException(status_code=403, detail={"error": "missing_role", "required": s.required_role})
    return principal
