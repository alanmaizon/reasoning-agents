"""API auth tests for Entra-protected /v1 endpoints."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import api
from src.security.entra_auth import EntraForbiddenError, EntraUnauthorizedError


class _AllowAllValidator:
    def validate_token(self, _token: str):
        return {"sub": "user-1"}


class _UnauthorizedValidator:
    def validate_token(self, _token: str):
        raise EntraUnauthorizedError("Bearer token validation failed.")


class _ForbiddenValidator:
    def validate_token(self, _token: str):
        raise EntraForbiddenError("Missing required scopes: api.access")


@pytest.fixture(autouse=True)
def _clear_auth_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("API_RATE_LIMIT_REQUESTS_PER_MINUTE", "0")
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    api._token_validator.cache_clear()
    api._state_store.cache_clear()
    api._rate_limiter.cache_clear()
    yield
    api._token_validator.cache_clear()
    api._state_store.cache_clear()
    api._rate_limiter.cache_clear()


def test_v1_requires_token_when_auth_enabled(monkeypatch):
    monkeypatch.setattr(api, "build_entra_validator", lambda: _AllowAllValidator())
    client = TestClient(api.app)

    response = client.get("/v1/state/auth-user")
    assert response.status_code == 401
    assert response.json()["detail"] == "Missing bearer token."


def test_v1_rejects_invalid_token(monkeypatch):
    monkeypatch.setattr(
        api, "build_entra_validator", lambda: _UnauthorizedValidator()
    )
    client = TestClient(api.app)

    response = client.get(
        "/v1/state/auth-user",
        headers={"Authorization": "Bearer bad-token"},
    )
    assert response.status_code == 401
    assert "validation failed" in response.json()["detail"].lower()


def test_v1_rejects_forbidden_token(monkeypatch):
    monkeypatch.setattr(api, "build_entra_validator", lambda: _ForbiddenValidator())
    client = TestClient(api.app)

    response = client.get(
        "/v1/state/auth-user",
        headers={"Authorization": "Bearer token"},
    )
    assert response.status_code == 403
    assert "required scopes" in response.json()["detail"].lower()


def test_v1_accepts_valid_token(monkeypatch):
    monkeypatch.setattr(api, "build_entra_validator", lambda: _AllowAllValidator())
    client = TestClient(api.app)

    response = client.get(
        "/v1/state/auth-user",
        headers={"Authorization": "Bearer good-token"},
    )
    assert response.status_code == 200
    assert response.json()["preferred_minutes"] == 30


def test_auth_mode_uses_claim_identity_instead_of_payload_user_id(monkeypatch):
    monkeypatch.setattr(api, "build_entra_validator", lambda: _AllowAllValidator())
    client = TestClient(api.app)

    start_response = client.post(
        "/v1/session/start",
        headers={"Authorization": "Bearer good-token"},
        json={
            "user_id": "spoofed-user",
            "focus_topics": ["Security"],
            "minutes": 15,
            "offline": True,
        },
    )
    assert start_response.status_code == 200
    payload = start_response.json()
    assert payload["user_id"] == "auth:user-1"

    state_response = client.get(
        "/v1/state/any-other-user",
        headers={"Authorization": "Bearer good-token"},
    )
    assert state_response.status_code == 200
    assert state_response.json()["preferred_minutes"] == 15
