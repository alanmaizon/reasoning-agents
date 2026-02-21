"""API smoke tests for hosted mode endpoints."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api import app


def test_healthz():
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_start_and_submit_offline():
    client = TestClient(app)

    start_response = client.post(
        "/v1/session/start",
        json={
            "user_id": "api-test-user",
            "focus_topics": ["Security"],
            "minutes": 20,
            "offline": True,
        },
    )
    assert start_response.status_code == 200
    start_payload = start_response.json()
    assert start_payload["offline_used"] is True
    assert len(start_payload["exam"]["questions"]) >= 8

    answers = {q["id"]: 0 for q in start_payload["exam"]["questions"]}
    submit_response = client.post(
        "/v1/session/submit",
        json={
            "user_id": "api-test-user",
            "exam": start_payload["exam"],
            "answers": {"answers": answers},
            "offline": True,
        },
    )
    assert submit_response.status_code == 200
    submit_payload = submit_response.json()
    assert "diagnosis" in submit_payload
    assert "coaching" in submit_payload
    assert "state" in submit_payload

    state_response = client.get("/v1/state/api-test-user")
    assert state_response.status_code == 200
    assert state_response.json()["preferred_minutes"] == 20
