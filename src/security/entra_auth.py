"""Microsoft Entra ID bearer token validation helpers."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple
from urllib.request import Request, urlopen
from urllib.parse import urlparse

import jwt
from jwt import InvalidTokenError
from jwt.algorithms import RSAAlgorithm


class EntraUnauthorizedError(Exception):
    """Raised when bearer token authentication fails."""


class EntraForbiddenError(Exception):
    """Raised when token is valid but missing required permissions."""


def _truthy(value: Optional[str]) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_csv(value: Optional[str]) -> Tuple[str, ...]:
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _normalize_issuer(value: str) -> str:
    return value.strip().rstrip("/")


def _append_unique(values: Tuple[str, ...], *items: str) -> Tuple[str, ...]:
    seen = {item for item in values if item}
    out = list(values)
    for item in items:
        if not item or item in seen:
            continue
        out.append(item)
        seen.add(item)
    return tuple(out)


@dataclass(frozen=True)
class EntraAuthConfig:
    tenant_id: Optional[str]
    issuers: Tuple[str, ...]
    jwks_uri: Optional[str]
    audiences: Tuple[str, ...]
    required_scopes: Tuple[str, ...]
    required_roles: Tuple[str, ...]
    timeout_seconds: float
    jwks_cache_ttl_seconds: int


class EntraTokenValidator:
    """Validates Entra access tokens using OpenID metadata and JWKS."""

    def __init__(self, config: EntraAuthConfig) -> None:
        self._config = config
        self._issuers = {item for item in config.issuers if item}
        self._normalized_issuers = {
            _normalize_issuer(item) for item in config.issuers if item
        }
        self._jwks_uri = config.jwks_uri
        self._jwks_keys: Dict[str, Any] = {}
        self._jwks_expires_at = 0.0

    def _well_known_url(self) -> str:
        if not self._config.tenant_id:
            raise EntraUnauthorizedError(
                "Authentication metadata is missing tenant information."
            )
        return (
            "https://login.microsoftonline.com/"
            f"{self._config.tenant_id}/v2.0/.well-known/openid-configuration"
        )

    def _fetch_json(self, url: str) -> Dict[str, Any]:
        try:
            request = Request(url, headers={"Accept": "application/json"})
            with urlopen(request, timeout=self._config.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
            return json.loads(raw)
        except Exception as exc:
            raise EntraUnauthorizedError(
                "Failed to fetch Entra authentication metadata."
            ) from exc

    def _ensure_metadata(self) -> None:
        if self._issuers and self._jwks_uri:
            return

        metadata = self._fetch_json(self._well_known_url())
        if not self._issuers and metadata.get("issuer"):
            self._issuers = {metadata["issuer"]}
        self._jwks_uri = self._jwks_uri or metadata.get("jwks_uri")
        if not self._issuers or not self._jwks_uri:
            raise EntraUnauthorizedError(
                "Incomplete Entra OpenID metadata (issuer/jwks_uri)."
            )

    def _refresh_jwks(self, force: bool = False) -> None:
        now = time.time()
        if (
            not force
            and self._jwks_keys
            and now < self._jwks_expires_at
        ):
            return

        self._ensure_metadata()
        metadata = self._fetch_json(self._jwks_uri)
        keys = metadata.get("keys", [])

        parsed: Dict[str, Any] = {}
        for jwk in keys:
            kid = jwk.get("kid")
            if not kid:
                continue
            try:
                parsed[kid] = RSAAlgorithm.from_jwk(json.dumps(jwk))
            except Exception:
                continue

        if not parsed:
            raise EntraUnauthorizedError(
                "No valid signing keys available for Entra token validation."
            )

        self._jwks_keys = parsed
        self._jwks_expires_at = now + self._config.jwks_cache_ttl_seconds

    def _signing_key(self, kid: str) -> Any:
        self._refresh_jwks(force=False)
        key = self._jwks_keys.get(kid)
        if key is not None:
            return key

        self._refresh_jwks(force=True)
        key = self._jwks_keys.get(kid)
        if key is None:
            raise EntraUnauthorizedError(
                "Token signing key not recognized."
            )
        return key

    def _enforce_permissions(self, claims: Dict[str, Any]) -> None:
        if self._config.required_scopes:
            granted_scopes = set((claims.get("scp") or "").split())
            missing_scopes = [
                scope
                for scope in self._config.required_scopes
                if scope not in granted_scopes
            ]
            if missing_scopes:
                raise EntraForbiddenError(
                    "Missing required scopes: " + ", ".join(missing_scopes)
                )

        if self._config.required_roles:
            raw_roles = claims.get("roles") or []
            if isinstance(raw_roles, str):
                raw_roles = [raw_roles]
            granted_roles = set(raw_roles)
            missing_roles = [
                role
                for role in self._config.required_roles
                if role not in granted_roles
            ]
            if missing_roles:
                raise EntraForbiddenError(
                    "Missing required roles: " + ", ".join(missing_roles)
                )

    def validate_token(self, token: str) -> Dict[str, Any]:
        if not token:
            raise EntraUnauthorizedError("Bearer token is missing.")

        try:
            header = jwt.get_unverified_header(token)
        except InvalidTokenError as exc:
            raise EntraUnauthorizedError(
                "Bearer token header is invalid."
            ) from exc

        kid = header.get("kid")
        if not kid:
            raise EntraUnauthorizedError("Bearer token does not include key id.")

        signing_key = self._signing_key(kid)

        try:
            claims = jwt.decode(
                token,
                key=signing_key,
                algorithms=["RS256"],
                audience=self._config.audiences,
                options={"require": ["exp"], "verify_iss": False},
            )
        except jwt.ExpiredSignatureError as exc:
            raise EntraUnauthorizedError("Bearer token has expired.") from exc
        except InvalidTokenError as exc:
            raise EntraUnauthorizedError(
                "Bearer token validation failed."
            ) from exc

        token_tenant = claims.get("tid")
        if (
            self._config.tenant_id
            and isinstance(token_tenant, str)
            and token_tenant
            and token_tenant != self._config.tenant_id
        ):
            raise EntraUnauthorizedError("Bearer token tenant is not allowed.")

        issuer = claims.get("iss")
        if not issuer or _normalize_issuer(issuer) not in self._normalized_issuers:
            raise EntraUnauthorizedError("Bearer token issuer is not allowed.")

        self._enforce_permissions(claims)
        return claims


def build_entra_validator() -> Optional[EntraTokenValidator]:
    """Build a validator from environment configuration."""
    if not _truthy(os.environ.get("ENTRA_AUTH_ENABLED")):
        return None

    audiences = _parse_csv(
        os.environ.get("ENTRA_AUDIENCE")
        or os.environ.get("ENTRA_AUDIENCES")
    )
    if not audiences:
        raise RuntimeError(
            "ENTRA_AUDIENCE (or ENTRA_AUDIENCES) is required when Entra auth is enabled."
        )

    tenant_id = os.environ.get("ENTRA_TENANT_ID")
    issuers = _parse_csv(
        os.environ.get("ENTRA_ISSUERS")
        or os.environ.get("ENTRA_ISSUER")
    )
    if not issuers and tenant_id:
        issuers = (
            f"https://login.microsoftonline.com/{tenant_id}/v2.0",
            f"https://sts.windows.net/{tenant_id}/",
        )

    # CIAM tokens can legitimately use the configured ciamlogin authority host.
    # Add this issuer family automatically when FRONTEND_AUTHORITY targets ciamlogin.
    frontend_authority = (os.environ.get("FRONTEND_AUTHORITY") or "").strip().rstrip("/")
    if tenant_id and frontend_authority:
        parsed = urlparse(frontend_authority)
        if ".ciamlogin.com" in (parsed.netloc or "").lower():
            ciam_issuer_by_id = f"{frontend_authority}/{tenant_id}/v2.0"
            issuers = _append_unique(
                issuers,
                ciam_issuer_by_id,
                f"{ciam_issuer_by_id}/",
            )

            tenant_domain = (os.environ.get("ENTRA_TENANT_DOMAIN") or "").strip()
            if tenant_domain:
                ciam_issuer_by_domain = f"{frontend_authority}/{tenant_domain}/v2.0"
                issuers = _append_unique(
                    issuers,
                    ciam_issuer_by_domain,
                    f"{ciam_issuer_by_domain}/",
                )

    jwks_uri = os.environ.get("ENTRA_JWKS_URI")

    if not tenant_id and not issuers:
        raise RuntimeError(
            "ENTRA_TENANT_ID or ENTRA_ISSUER(S) is required when Entra auth is enabled."
        )
    if not tenant_id and not jwks_uri:
        raise RuntimeError(
            "ENTRA_JWKS_URI is required when ENTRA_TENANT_ID is not provided."
        )

    config = EntraAuthConfig(
        tenant_id=tenant_id,
        issuers=issuers,
        jwks_uri=jwks_uri,
        audiences=audiences,
        required_scopes=_parse_csv(os.environ.get("ENTRA_REQUIRED_SCOPES")),
        required_roles=_parse_csv(os.environ.get("ENTRA_REQUIRED_ROLES")),
        timeout_seconds=float(os.environ.get("ENTRA_HTTP_TIMEOUT_SECONDS", "5")),
        jwks_cache_ttl_seconds=int(
            os.environ.get("ENTRA_JWKS_CACHE_TTL_SECONDS", "3600")
        ),
    )
    return EntraTokenValidator(config)
