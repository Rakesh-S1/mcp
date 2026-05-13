"""
Azure AD JWT validation middleware for the MCP HTTP server.

How it works:
- Every HTTP request to the MCP server must carry an Authorization header:
      Authorization: Bearer <token>
- The token must be issued by YOUR Azure AD tenant (checked via `iss` claim).
- The token audience (`aud`) must match the MCP server's App Registration
  client-id (set via env var MCP_SERVER_CLIENT_ID).
- The token signature is verified against Azure AD's public JWKS keys
  (fetched once and cached).

This guarantees:
  ✅ Only identities in YOUR tenant can authenticate.
  ✅ Tokens from other tenants are rejected.
  ✅ Unauthenticated requests get 401.
  ✅ The MCP server never has to trust network perimeter alone.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

import httpx
from jose import JWTError, jwt
from jose.backends import RSAKey
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


# ---------------------------------------------------------------------------
# JWKS key fetching (cached — refreshed on process restart)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _get_jwks(tenant_id: str) -> dict[str, Any]:
    """Fetch Azure AD public keys for token signature verification."""
    url = (
        f"https://login.microsoftonline.com/{tenant_id}"
        f"/discovery/v2.0/keys"
    )
    with httpx.Client(timeout=10) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.json()


def _find_key(tenant_id: str, kid: str) -> dict | None:
    """Return the JWK matching the given key-id, or None."""
    jwks = _get_jwks(tenant_id)
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            return key
    return None


# ---------------------------------------------------------------------------
# Token validation
# ---------------------------------------------------------------------------

def validate_token(token: str) -> dict[str, Any]:
    """
    Validate an Azure AD JWT and return its claims.

    Raises ValueError with a descriptive message on any failure.
    """
    tenant_id = os.environ["AZURE_TENANT_ID"]
    expected_audience = os.environ["MCP_SERVER_CLIENT_ID"]  # App Registration client-id

    valid_issuers = [
        f"https://login.microsoftonline.com/{tenant_id}/v2.0",
        f"https://sts.windows.net/{tenant_id}/",
    ]

    # Decode header without verification to get `kid`
    try:
        unverified_header = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise ValueError(f"Malformed token header: {exc}") from exc

    kid = unverified_header.get("kid")
    if not kid:
        raise ValueError("Token header missing 'kid'")

    jwk = _find_key(tenant_id, kid)
    if jwk is None:
        # Key might have rotated — bust cache and retry once
        _get_jwks.cache_clear()
        jwk = _find_key(tenant_id, kid)
    if jwk is None:
        raise ValueError(f"Public key '{kid}' not found in Azure AD JWKS")

    # Full verification: signature + issuer + audience + expiry
    try:
        claims = jwt.decode(
            token,
            jwk,
            algorithms=["RS256"],
            audience=expected_audience,
            issuer=valid_issuers,  # jose accepts a list
            options={"verify_exp": True},
        )
    except JWTError as exc:
        raise ValueError(f"Token validation failed: {exc}") from exc

    return claims


# ---------------------------------------------------------------------------
# Starlette middleware
# ---------------------------------------------------------------------------

class AzureADAuthMiddleware(BaseHTTPMiddleware):
    """
    Starlette middleware that enforces Azure AD bearer-token authentication.

    Health-check path (/health) is exempt so load-balancers can probe it.

    Set SKIP_AUTH=true to bypass token validation entirely.
    This is for local testing ONLY — never set this in production.
    """

    EXEMPT_PATHS = {"/health"}

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self.EXEMPT_PATHS:
            return await call_next(request)

        # ── Local testing escape hatch ────────────────────────────────────
        if os.environ.get("SKIP_AUTH", "").lower() in ("1", "true", "yes"):
            request.state.azure_claims = {"oid": "mock-user", "skip_auth": True}
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                {"error": "Missing or invalid Authorization header"},
                status_code=401,
            )

        token = auth_header[len("Bearer "):]
        try:
            claims = validate_token(token)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=401)

        # Attach claims to request state for downstream use (logging, etc.)
        request.state.azure_claims = claims
        return await call_next(request)
