"""Tests for MCP tool policy allow-list behavior."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.orchestration.tool_policy import ALLOWED_MCP_TOOLS, approval_handler, is_tool_allowed


def test_mcp_allowlist_includes_code_sample_search():
    assert "microsoft_docs_search" in ALLOWED_MCP_TOOLS
    assert "microsoft_docs_fetch" in ALLOWED_MCP_TOOLS
    assert "microsoft_code_sample_search" in ALLOWED_MCP_TOOLS


def test_approval_handler_denies_unknown_tool():
    approved, reason = approval_handler("not_a_real_tool")
    assert approved is False
    assert "not_a_real_tool" in reason
    assert is_tool_allowed("not_a_real_tool") is False
