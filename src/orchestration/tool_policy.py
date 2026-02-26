"""MCP tool allow-list and approval handler."""

from __future__ import annotations

import re
from typing import Set

# Only read-only Microsoft Learn tools are permitted
ALLOWED_MCP_TOOLS: Set[str] = {
    "microsoft_docs_search",
    "microsoft_docs_fetch",
    "microsoft_code_sample_search",
}
# Restrict tool names to short, simple identifiers to block metacharacter tricks.
_TOOL_NAME_RE = re.compile(r"^[a-z0-9_]{1,64}$")


def _normalize_tool_name(tool_name: str) -> str:
    return " ".join(tool_name.split()).lower()


def _is_normalized_tool_allowed(normalized_tool_name: str) -> bool:
    if not _TOOL_NAME_RE.fullmatch(normalized_tool_name):
        return False
    return normalized_tool_name in ALLOWED_MCP_TOOLS


def is_tool_allowed(tool_name: str) -> bool:
    """Return True only for allow-listed tools."""
    normalized = _normalize_tool_name(tool_name)
    return _is_normalized_tool_allowed(normalized)


def approval_handler(tool_name: str) -> tuple[bool, str]:
    """Auto-approve allow-listed tools; deny everything else.

    Returns (approved, reason).
    """
    normalized = _normalize_tool_name(tool_name)
    if _is_normalized_tool_allowed(normalized):
        return True, "auto-approved (read-only allowlist)"
    return False, f"Tool '{normalized}' is not in the allowlist: {ALLOWED_MCP_TOOLS}"
