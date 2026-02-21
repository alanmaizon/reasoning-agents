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


class StateStore:
    """Load/save student state from Azure Blob (if configured) or local disk."""

    def __init__(self) -> None:
        self._local_dir = Path(os.environ.get("STATE_DIR", ".data/state"))
        self._conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
        self._container_name = os.environ.get(
            "AZURE_STORAGE_CONTAINER", "mdt-data"
        )
        self._blob_prefix = os.environ.get("STATE_BLOB_PREFIX", "state")

    def load(self, user_id: str) -> StudentState:
        key = _sanitize_user_id(user_id)
        payload = self._load_blob_payload(key) if self._conn_str else None
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
