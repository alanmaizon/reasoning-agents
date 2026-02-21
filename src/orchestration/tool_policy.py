"""MCP tool allow-list and approval handler."""

from __future__ import annotations

from typing import Set

# Only read-only Microsoft Learn tools are permitted
ALLOWED_MCP_TOOLS: Set[str] = {
    "microsoft_docs_search",
    "microsoft_docs_fetch",
    "microsoft_code_sample_search",
}


def is_tool_allowed(tool_name: str) -> bool:
    """Return True only for allow-listed tools."""
    return tool_name in ALLOWED_MCP_TOOLS


def approval_handler(tool_name: str) -> tuple[bool, str]:
    """Auto-approve allow-listed tools; deny everything else.

    Returns (approved, reason).
    """
    if is_tool_allowed(tool_name):
        return True, "auto-approved (read-only allowlist)"
    return False, f"Tool '{tool_name}' is not in the allowlist: {ALLOWED_MCP_TOOLS}"
