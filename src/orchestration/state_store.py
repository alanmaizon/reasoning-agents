"""State persistence abstraction for local and Azure-hosted runtimes."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import ValidationError

from ..models.state import StudentState


def _sanitize_user_id(user_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", user_id.strip())
    cleaned = cleaned.strip("._")
    return cleaned or "default"


def _normalize_table_name(name: str) -> str:
    candidate = (name or "").strip()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", candidate):
        return candidate
    return "student_state"


def _build_pg_conninfo() -> Optional[str]:
    dsn = os.environ.get("POSTGRES_DSN") or os.environ.get("DATABASE_URL")
    if dsn:
        return dsn

    host = os.environ.get("POSTGRES_HOST")
    dbname = os.environ.get("POSTGRES_DB")
    user = os.environ.get("POSTGRES_USER")
    password = os.environ.get("POSTGRES_PASSWORD")
    if not (host and dbname and user and password):
        return None

    port = os.environ.get("POSTGRES_PORT", "5432")
    sslmode = os.environ.get("POSTGRES_SSLMODE", "require")
    return (
        f"host={host} "
        f"port={port} "
        f"dbname={dbname} "
        f"user={user} "
        f"password={password} "
        f"sslmode={sslmode}"
    )


class StateStore:
    """Load/save student state from Postgres, Blob, or local disk."""

    def __init__(self) -> None:
        self._local_dir = Path(os.environ.get("STATE_DIR", ".data/state"))
        self._pg_conninfo = _build_pg_conninfo()
        self._pg_table = _normalize_table_name(
            os.environ.get("STATE_PG_TABLE", "student_state")
        )
        self._pg_schema_ready = False
        self._conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
        self._container_name = os.environ.get(
            "AZURE_STORAGE_CONTAINER", "mdt-data"
        )
        self._blob_prefix = os.environ.get("STATE_BLOB_PREFIX", "state")

    def load(self, user_id: str) -> StudentState:
        key = _sanitize_user_id(user_id)
        payload = self._load_pg_payload(key) if self._pg_conninfo else None
        if payload is None and self._conn_str:
            payload = self._load_blob_payload(key)
        if payload is None:
            payload = self._load_local_payload(key)

        if not payload:
            return StudentState()
        try:
            return StudentState.model_validate(payload)
        except ValidationError:
            return StudentState()

    def save(self, user_id: str, state: StudentState) -> None:
        key = _sanitize_user_id(user_id)
        payload = state.model_dump()
        if self._pg_conninfo and self._save_pg_payload(key, payload):
            return
        if self._conn_str and self._save_blob_payload(key, payload):
            return
        self._save_local_payload(key, payload)

    def _local_path(self, key: str) -> Path:
        return self._local_dir / f"{key}.json"

    def _load_local_payload(self, key: str) -> Dict[str, Any]:
        path = self._local_path(key)
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_local_payload(self, key: str, payload: Dict[str, Any]) -> None:
        self._local_dir.mkdir(parents=True, exist_ok=True)
        path = self._local_path(key)
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _blob_name(self, key: str) -> str:
        return f"{self._blob_prefix}/{key}.json"

    def _get_pg_connection(self):
        if not self._pg_conninfo:
            return None
        try:
            import psycopg
        except ImportError:
            return None
        try:
            return psycopg.connect(self._pg_conninfo, autocommit=True)
        except Exception:
            return None

    def _ensure_pg_schema(self, conn) -> bool:
        if self._pg_schema_ready:
            return True
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._pg_table} (
                        user_id TEXT PRIMARY KEY,
                        state JSONB NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            self._pg_schema_ready = True
            return True
        except Exception:
            return False

    def _load_pg_payload(self, key: str) -> Optional[Dict[str, Any]]:
        conn = self._get_pg_connection()
        if conn is None:
            return None

        try:
            if not self._ensure_pg_schema(conn):
                return None
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT state FROM {self._pg_table} WHERE user_id = %s",
                    (key,),
                )
                row = cur.fetchone()
            if not row:
                return None

            raw = row[0]
            if isinstance(raw, dict):
                return raw
            if isinstance(raw, (bytes, bytearray)):
                return json.loads(raw.decode("utf-8"))
            if isinstance(raw, str):
                return json.loads(raw)
            return None
        except Exception:
            return None
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _save_pg_payload(self, key: str, payload: Dict[str, Any]) -> bool:
        conn = self._get_pg_connection()
        if conn is None:
            return False

        try:
            if not self._ensure_pg_schema(conn):
                return False
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self._pg_table} (user_id, state, updated_at)
                    VALUES (%s, %s::jsonb, NOW())
                    ON CONFLICT (user_id)
                    DO UPDATE SET
                      state = EXCLUDED.state,
                      updated_at = NOW()
                    """,
                    (key, json.dumps(payload, ensure_ascii=False)),
                )
            return True
        except Exception:
            return False
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _get_blob_container_client(self):
        if not self._conn_str:
            return None
        try:
            from azure.storage.blob import BlobServiceClient
        except ImportError:
            return None

        client = BlobServiceClient.from_connection_string(self._conn_str)
        return client.get_container_client(self._container_name)

    def _load_blob_payload(self, key: str) -> Optional[Dict[str, Any]]:
        container = self._get_blob_container_client()
        if container is None:
            return None

        blob = container.get_blob_client(self._blob_name(key))
        try:
            data = blob.download_blob().readall()
            return json.loads(data.decode("utf-8"))
        except Exception:
            return None

    def _save_blob_payload(self, key: str, payload: Dict[str, Any]) -> bool:
        container = self._get_blob_container_client()
        if container is None:
            return False

        try:
            if not container.exists():
                container.create_container()
            blob = container.get_blob_client(self._blob_name(key))
            blob.upload_blob(
                json.dumps(payload, indent=2, ensure_ascii=False),
                overwrite=True,
            )
            return True
        except Exception:
            return False
