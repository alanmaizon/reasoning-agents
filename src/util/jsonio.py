"""JSON I/O helpers with defensive parsing."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict


def load_json(path: Path) -> Dict[str, Any]:
    """Load JSON from a file, returning empty dict on missing/corrupt file."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_json(path: Path, data: Dict[str, Any]) -> None:
    """Persist dict as pretty-printed JSON."""
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def extract_json(raw: str) -> Dict[str, Any]:
    """Extract the first JSON object from a possibly-noisy string.

    Handles common LLM quirks: markdown fences, leading prose, etc.
    Raises ``ValueError`` if no valid JSON object is found.
    """
    # Strip markdown code fences
    cleaned = re.sub(r"```(?:json)?", "", raw).strip()
    # Try full string first
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # Find first { ... } block
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Could not extract JSON from agent output: {raw[:200]}")
