"""Unit tests for Foundry MCP connection-name wiring."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.foundry_client import FoundryRunner


class _DummyConnections:
    def __init__(self) -> None:
        self.invoke_calls = []
        self.list_calls = []

    def invoke_tool(self, **kwargs):
        self.invoke_calls.append(kwargs)
        return {"ok": True}

    def list_tools(self, **kwargs):
        self.list_calls.append(kwargs)
        return {"tools": [{"name": "microsoft_docs_search"}]}


class _DummyClient:
    def __init__(self) -> None:
        self.connections = _DummyConnections()


def test_connections_path_passes_connection_name_when_configured():
    client = _DummyClient()
    runner = FoundryRunner(
        client=client,
        deployment="test-model",
        mcp_connection_name="learn-mcp",
    )

    runner.run_mcp_tool("microsoft_docs_search", {"query": "az-900"})
    runner.list_mcp_tools()

    assert client.connections.invoke_calls
    assert client.connections.invoke_calls[0]["connection_name"] == "learn-mcp"
    assert client.connections.list_calls
    assert client.connections.list_calls[0]["connection_name"] == "learn-mcp"


def test_connections_path_omits_connection_name_when_unset():
    client = _DummyClient()
    runner = FoundryRunner(client=client, deployment="test-model", mcp_connection_name=None)

    runner.run_mcp_tool("microsoft_docs_search", {"query": "az-900"})
    runner.list_mcp_tools()

    assert client.connections.invoke_calls
    assert "connection_name" not in client.connections.invoke_calls[0]
    assert client.connections.list_calls
    assert "connection_name" not in client.connections.list_calls[0]
