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


def test_approval_handler_normalizes_tool_name():
    approved, _ = approval_handler("  MICROSOFT_DOCS_SEARCH  ")
    assert approved is True


def test_is_tool_allowed_rejects_injection_like_name():
    assert is_tool_allowed("microsoft_docs_search; rm -rf /") is False
    assert is_tool_allowed("microsoft_docs_search\nmicrosoft_docs_fetch") is False
    assert is_tool_allowed("$(microsoft_docs_search)") is False


def test_tool_policy_fail_closed_on_non_string_input():
    inputs = [
        None,
        123,
        ["microsoft_docs_search"],
        {"name": "microsoft_docs_search"},
    ]
    for value in inputs:
        assert is_tool_allowed(value) is False
        approved, _ = approval_handler(value)
        assert approved is False
