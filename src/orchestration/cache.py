"""Simple disk-backed URL cache for fetched Learn docs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

_CACHE_PATH = Path("cache.json")


def _load() -> Dict[str, Any]:
    if _CACHE_PATH.exists():
        try:
            return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save(data: Dict[str, Any]) -> None:
    _CACHE_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def cache_get(url: str) -> Optional[str]:
    """Return cached content for *url* or None."""
    return _load().get(url)


def cache_put(url: str, content: str) -> None:
    """Store *content* for *url* on disk."""
    data = _load()
    data[url] = content
    _save(data)
