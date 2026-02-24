"""Unit tests for Entra auth helper behavior."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.security import entra_auth


def test_build_validator_defaults_v1_and_v2_issuers(monkeypatch):
    monkeypatch.setenv("ENTRA_AUTH_ENABLED", "true")
    monkeypatch.setenv("ENTRA_TENANT_ID", "tid-123")
    monkeypatch.setenv("ENTRA_AUDIENCE", "api://demo")
    monkeypatch.delenv("ENTRA_ISSUER", raising=False)
    monkeypatch.delenv("ENTRA_ISSUERS", raising=False)

    validator = entra_auth.build_entra_validator()
    assert validator is not None
    assert "https://login.microsoftonline.com/tid-123/v2.0" in validator._issuers
    assert "https://sts.windows.net/tid-123/" in validator._issuers


def test_validate_token_accepts_allowed_issuer(monkeypatch):
    cfg = entra_auth.EntraAuthConfig(
        tenant_id=None,
        issuers=("iss-allowed",),
        jwks_uri="https://example.test/keys",
        audiences=("api://demo",),
        required_scopes=(),
        required_roles=(),
        timeout_seconds=1.0,
        jwks_cache_ttl_seconds=60,
    )
    validator = entra_auth.EntraTokenValidator(cfg)

    monkeypatch.setattr(
        entra_auth.jwt, "get_unverified_header", lambda _token: {"kid": "k1"}
    )
    monkeypatch.setattr(validator, "_signing_key", lambda _kid: "fake-key")
    monkeypatch.setattr(
        entra_auth.jwt,
        "decode",
        lambda *args, **kwargs: {
            "iss": "iss-allowed",
            "aud": "api://demo",
            "exp": 9999999999,
        },
    )

    claims = validator.validate_token("token")
    assert claims["iss"] == "iss-allowed"


def test_validate_token_rejects_unexpected_issuer(monkeypatch):
    cfg = entra_auth.EntraAuthConfig(
        tenant_id=None,
        issuers=("iss-allowed",),
        jwks_uri="https://example.test/keys",
        audiences=("api://demo",),
        required_scopes=(),
        required_roles=(),
        timeout_seconds=1.0,
        jwks_cache_ttl_seconds=60,
    )
    validator = entra_auth.EntraTokenValidator(cfg)

    monkeypatch.setattr(
        entra_auth.jwt, "get_unverified_header", lambda _token: {"kid": "k1"}
    )
    monkeypatch.setattr(validator, "_signing_key", lambda _kid: "fake-key")
    monkeypatch.setattr(
        entra_auth.jwt,
        "decode",
        lambda *args, **kwargs: {
            "iss": "iss-unexpected",
            "aud": "api://demo",
            "exp": 9999999999,
        },
    )

    with pytest.raises(entra_auth.EntraUnauthorizedError):
        validator.validate_token("token")


def test_validate_token_allows_issuer_variant_when_tenant_matches(monkeypatch):
    cfg = entra_auth.EntraAuthConfig(
        tenant_id="tid-123",
        issuers=("iss-allowed",),
        jwks_uri="https://example.test/keys",
        audiences=("api://demo",),
        required_scopes=(),
        required_roles=(),
        timeout_seconds=1.0,
        jwks_cache_ttl_seconds=60,
    )
    validator = entra_auth.EntraTokenValidator(cfg)

    monkeypatch.setattr(
        entra_auth.jwt, "get_unverified_header", lambda _token: {"kid": "k1"}
    )
    monkeypatch.setattr(validator, "_signing_key", lambda _kid: "fake-key")
    monkeypatch.setattr(
        entra_auth.jwt,
        "decode",
        lambda *args, **kwargs: {
            "iss": "iss-provider-specific-variant",
            "tid": "tid-123",
            "aud": "api://demo",
            "exp": 9999999999,
        },
    )

    claims = validator.validate_token("token")
    assert claims["tid"] == "tid-123"


def test_validate_token_rejects_mismatched_tenant_even_if_signature_valid(monkeypatch):
    cfg = entra_auth.EntraAuthConfig(
        tenant_id="tid-expected",
        issuers=("iss-allowed",),
        jwks_uri="https://example.test/keys",
        audiences=("api://demo",),
        required_scopes=(),
        required_roles=(),
        timeout_seconds=1.0,
        jwks_cache_ttl_seconds=60,
    )
    validator = entra_auth.EntraTokenValidator(cfg)

    monkeypatch.setattr(
        entra_auth.jwt, "get_unverified_header", lambda _token: {"kid": "k1"}
    )
    monkeypatch.setattr(validator, "_signing_key", lambda _kid: "fake-key")
    monkeypatch.setattr(
        entra_auth.jwt,
        "decode",
        lambda *args, **kwargs: {
            "iss": "iss-allowed",
            "tid": "tid-other",
            "aud": "api://demo",
            "exp": 9999999999,
        },
    )

    with pytest.raises(entra_auth.EntraUnauthorizedError):
        validator.validate_token("token")


def test_validate_token_accepts_guid_audience_for_api_uri_config(monkeypatch):
    cfg = entra_auth.EntraAuthConfig(
        tenant_id=None,
        issuers=("iss-allowed",),
        jwks_uri="https://example.test/keys",
        audiences=("api://57c7640f-4b41-4281-9a04-057aecab8876",),
        required_scopes=(),
        required_roles=(),
        timeout_seconds=1.0,
        jwks_cache_ttl_seconds=60,
    )
    validator = entra_auth.EntraTokenValidator(cfg)

    monkeypatch.setattr(
        entra_auth.jwt, "get_unverified_header", lambda _token: {"kid": "k1"}
    )
    monkeypatch.setattr(validator, "_signing_key", lambda _kid: "fake-key")
    monkeypatch.setattr(
        entra_auth.jwt,
        "decode",
        lambda *args, **kwargs: {
            "iss": "iss-allowed",
            "aud": "57c7640f-4b41-4281-9a04-057aecab8876",
            "exp": 9999999999,
        },
    )

    claims = validator.validate_token("token")
    assert claims["aud"] == "57c7640f-4b41-4281-9a04-057aecab8876"
