"""Rate limit tests for /v1 endpoints."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import api


class _AllowAllValidator:
    def validate_token(self, _token: str):
        return {"sub": "rate-limit-user"}


@pytest.fixture(autouse=True)
def _clear_caches(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    api._token_validator.cache_clear()
    api._state_store.cache_clear()
    api._rate_limiter.cache_clear()
    yield
    api._token_validator.cache_clear()
    api._state_store.cache_clear()
    api._rate_limiter.cache_clear()


def test_rate_limit_blocks_after_threshold(monkeypatch):
    monkeypatch.setattr(api, "build_entra_validator", lambda: None)
    monkeypatch.setenv("API_RATE_LIMIT_REQUESTS_PER_MINUTE", "1")
    monkeypatch.setenv("API_RATE_LIMIT_WINDOW_SECONDS", "60")
    api._rate_limiter.cache_clear()

    client = TestClient(api.app)
    first = client.post(
        "/v1/session/start",
        json={
            "user_id": "rate-test",
            "focus_topics": ["Security"],
            "minutes": 10,
            "offline": True,
        },
    )
    assert first.status_code == 200

    second = client.post(
        "/v1/session/start",
        json={
            "user_id": "rate-test",
            "focus_topics": ["Security"],
            "minutes": 10,
            "offline": True,
        },
    )
    assert second.status_code == 429
    assert second.headers.get("Retry-After")


def test_rate_limit_is_per_authenticated_principal(monkeypatch):
    monkeypatch.setattr(api, "build_entra_validator", lambda: _AllowAllValidator())
    monkeypatch.setenv("API_RATE_LIMIT_REQUESTS_PER_MINUTE", "1")
    monkeypatch.setenv("API_RATE_LIMIT_WINDOW_SECONDS", "60")
    api._rate_limiter.cache_clear()

    client = TestClient(api.app)
    first = client.get(
        "/v1/state/ignored-user",
        headers={"Authorization": "Bearer good-token"},
    )
    assert first.status_code == 200

    second = client.get(
        "/v1/state/ignored-user",
        headers={"Authorization": "Bearer good-token"},
    )
    assert second.status_code == 429
