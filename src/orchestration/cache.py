"""Simple disk-backed URL cache for fetched Learn docs."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

_CACHE_PATH = Path("cache.json")
_CACHE_BLOB_NAME = os.environ.get("CACHE_BLOB_NAME", "cache/cache.json")
_STORAGE_CONN_STR = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
_STORAGE_CONTAINER = os.environ.get("AZURE_STORAGE_CONTAINER", "mdt-data")


def _get_blob_container_client():
    if not _STORAGE_CONN_STR:
        return None
    try:
        from azure.storage.blob import BlobServiceClient
    except ImportError:
        return None

    client = BlobServiceClient.from_connection_string(_STORAGE_CONN_STR)
    return client.get_container_client(_STORAGE_CONTAINER)


def _load_local() -> Dict[str, Any]:
    if _CACHE_PATH.exists():
        try:
            return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_local(data: Dict[str, Any]) -> None:
    _CACHE_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _load_blob() -> Optional[Dict[str, Any]]:
    container = _get_blob_container_client()
    if container is None:
        return None
    blob = container.get_blob_client(_CACHE_BLOB_NAME)
    try:
        raw = blob.download_blob().readall()
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def _save_blob(data: Dict[str, Any]) -> bool:
    container = _get_blob_container_client()
    if container is None:
        return False
    try:
        if not container.exists():
            container.create_container()
        blob = container.get_blob_client(_CACHE_BLOB_NAME)
        blob.upload_blob(
            json.dumps(data, indent=2, ensure_ascii=False),
            overwrite=True,
        )
        return True
    except Exception:
        return False


def _load() -> Dict[str, Any]:
    blob_data = _load_blob()
    if blob_data is not None:
        return blob_data
    return _load_local()


def _save(data: Dict[str, Any]) -> None:
    if _save_blob(data):
        return
    _save_local(data)


def cache_get(url: str) -> Optional[str]:
    """Return cached content for *url* or None."""
    return _load().get(url)


def cache_put(url: str, content: str) -> None:
    """Store *content* for *url* on disk."""
    data = _load()
    data[url] = content
    _save(data)
