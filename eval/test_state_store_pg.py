"""StateStore tests for PostgreSQL primary persistence path."""

from __future__ import annotations

import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.models.state import StudentState
from src.orchestration.state_store import StateStore


class _FakeCursor:
    def __init__(self, rows: dict):
        self._rows = rows
        self._result = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query: str, params=None) -> None:
        normalized = " ".join(query.split()).lower()
        if normalized.startswith("create table"):
            return

        if normalized.startswith("select state from"):
            key = params[0]
            payload = self._rows.get(key)
            self._result = None if payload is None else (json.dumps(payload),)
            return

        if normalized.startswith("insert into"):
            key = params[0]
            payload_raw = params[1]
            self._rows[key] = json.loads(payload_raw)
            self._result = None
            return

        raise AssertionError(f"Unexpected SQL in test fake: {query}")

    def fetchone(self):
        return self._result


class _FakeConnection:
    def __init__(self, rows: dict):
        self._rows = rows
        self.closed = False

    def cursor(self):
        return _FakeCursor(self._rows)

    def close(self) -> None:
        self.closed = True


class _FakePsycopg:
    def __init__(self):
        self.rows = {}

    def connect(self, _conninfo, autocommit=False):
        assert autocommit is True
        return _FakeConnection(self.rows)


class _BrokenPsycopg:
    def connect(self, _conninfo, autocommit=False):
        raise RuntimeError("db unavailable")


def test_pg_primary_round_trip(monkeypatch, tmp_path):
    fake = _FakePsycopg()
    monkeypatch.setitem(sys.modules, "psycopg", fake)
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://fake")
    monkeypatch.setenv("STATE_DIR", str(tmp_path))

    store = StateStore()
    state = StudentState(preferred_minutes=45)
    store.save("alice@example.com", state)

    loaded = store.load("alice@example.com")
    assert loaded.preferred_minutes == 45
    assert "alice_example.com" in fake.rows
    assert not (tmp_path / "alice_example.com.json").exists()


def test_pg_failure_falls_back_to_local(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "psycopg", _BrokenPsycopg())
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://fake")
    monkeypatch.setenv("STATE_DIR", str(tmp_path))

    store = StateStore()
    state = StudentState(preferred_minutes=55)
    store.save("bob", state)

    path = tmp_path / "bob.json"
    assert path.exists()
    loaded = store.load("bob")
    assert loaded.preferred_minutes == 55


def test_pg_conninfo_builder_from_env(monkeypatch, tmp_path):
    fake = _FakePsycopg()
    monkeypatch.setitem(sys.modules, "psycopg", fake)
    monkeypatch.delenv("POSTGRES_DSN", raising=False)
    monkeypatch.setenv("POSTGRES_HOST", "pg.example.internal")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_DB", "mdt")
    monkeypatch.setenv("POSTGRES_USER", "mdtadmin")
    monkeypatch.setenv("POSTGRES_PASSWORD", "test-password")
    monkeypatch.setenv("POSTGRES_SSLMODE", "require")
    monkeypatch.setenv("STATE_PG_TABLE", "student_state")
    monkeypatch.setenv("STATE_DIR", str(tmp_path))

    store = StateStore()
    assert "host=pg.example.internal" in store._pg_conninfo
    assert "dbname=mdt" in store._pg_conninfo

    store.save("carol", StudentState(preferred_minutes=35))
    loaded = store.load("carol")
    assert loaded.preferred_minutes == 35
