"""Unit tests for Foundry MCP connection-name wiring."""

from __future__ import annotations

import importlib
import sys
from types import ModuleType
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.foundry_client as foundry_client
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


@pytest.fixture
def _clear_foundry_env(monkeypatch):
    for name in (
        "AZURE_AI_PROJECT_ENDPOINT",
        "AZURE_AI_MODEL_DEPLOYMENT_NAME",
        "AZURE_OPENAI_API_KEY",
        "MCP_PROJECT_CONNECTION_NAME",
        "MCP_SERVER_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def _install_fake_projects_sdk(monkeypatch, client_factory):
    azure_mod = ModuleType("azure")
    ai_mod = ModuleType("azure.ai")
    projects_mod = ModuleType("azure.ai.projects")
    identity_mod = ModuleType("azure.identity")

    class _DefaultAzureCredential:
        pass

    projects_mod.AIProjectClient = client_factory
    identity_mod.DefaultAzureCredential = _DefaultAzureCredential

    azure_mod.ai = ai_mod
    ai_mod.projects = projects_mod

    monkeypatch.setitem(sys.modules, "azure", azure_mod)
    monkeypatch.setitem(sys.modules, "azure.ai", ai_mod)
    monkeypatch.setitem(sys.modules, "azure.ai.projects", projects_mod)
    monkeypatch.setitem(sys.modules, "azure.identity", identity_mod)
    importlib.invalidate_caches()


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


def test_get_foundry_runner_prefers_projects_when_mcp_requested(
    monkeypatch, _clear_foundry_env
):
    monkeypatch.setenv(
        "AZURE_AI_PROJECT_ENDPOINT",
        "https://example.services.ai.azure.com/api/projects/demo-project",
    )
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-test")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("MCP_PROJECT_CONNECTION_NAME", "learn-mcp")

    class _ProjectsClient:
        def __init__(self, endpoint, credential):
            self.endpoint = endpoint
            self.credential = credential

        def get_openai_client(self):
            raise AssertionError("projects.get_openai_client should not be used here")

    _install_fake_projects_sdk(monkeypatch, _ProjectsClient)

    seen = {}

    def _direct_builder(endpoint):
        seen["endpoint"] = endpoint
        return object()

    monkeypatch.setattr(foundry_client, "_build_direct_azure_openai_client", _direct_builder)

    runner = foundry_client.get_foundry_runner()

    assert runner is not None
    assert isinstance(runner.client, _ProjectsClient)
    assert runner.mcp_connection_name == "learn-mcp"
    assert runner.openai_client is not None
    assert seen["endpoint"] == "https://example.openai.azure.com"


def test_get_foundry_runner_resolves_mcp_connection_metadata(
    monkeypatch, _clear_foundry_env
):
    monkeypatch.setenv(
        "AZURE_AI_PROJECT_ENDPOINT",
        "https://example.services.ai.azure.com/api/projects/demo-project",
    )
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-test")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("MCP_PROJECT_CONNECTION_NAME", "learn-mcp")

    class _Connections:
        def __init__(self) -> None:
            self.calls = []

        def get(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "id": "/subscriptions/000/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/acc/projects/proj/connections/learn-mcp",
                "name": "learn-mcp",
                "target": "https://learn.microsoft.com/api/mcp",
            }

    class _ProjectsClient:
        def __init__(self, endpoint, credential):
            self.endpoint = endpoint
            self.credential = credential
            self.connections = _Connections()

        def get_openai_client(self):
            raise AssertionError("projects.get_openai_client should not be used here")

    _install_fake_projects_sdk(monkeypatch, _ProjectsClient)
    monkeypatch.setattr(
        foundry_client,
        "_build_direct_azure_openai_client",
        lambda _endpoint: object(),
    )

    runner = foundry_client.get_foundry_runner()

    assert runner is not None
    assert isinstance(runner.client, _ProjectsClient)
    assert runner.mcp_connection_name == "learn-mcp"
    assert runner.mcp_connection_id.endswith("/connections/learn-mcp")
    assert runner.mcp_server_url == "https://learn.microsoft.com/api/mcp"
    assert runner.mcp_server_label == "learn-mcp"
    assert runner.client.connections.calls
    assert runner.client.connections.calls[0]["name"] == "learn-mcp"


def test_run_mcp_tool_uses_responses_with_project_connection():
    class _Responses:
        def __init__(self) -> None:
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)

            class _Response:
                output = [
                    {
                        "type": "mcp_call",
                        "name": "microsoft_docs_search",
                        "output": {
                            "results": [
                                {
                                    "title": "Shared responsibility in the cloud",
                                    "url": "https://learn.microsoft.com/en-us/azure/security/fundamentals/shared-responsibility",
                                    "snippet": "Responsibilities vary by service type.",
                                }
                            ]
                        },
                    }
                ]

            return _Response()

    class _ProjectOpenAIClient:
        def __init__(self):
            self.responses = _Responses()

    class _ProjectsClient:
        def __init__(self, openai_client):
            self._openai_client = openai_client

        def get_openai_client(self):
            return self._openai_client

    project_openai_client = _ProjectOpenAIClient()
    runner = FoundryRunner(
        client=_ProjectsClient(project_openai_client),
        deployment="gpt-test",
        mcp_connection_name="learn-mcp",
        mcp_connection_id="/subscriptions/000/.../connections/learn-mcp",
        mcp_server_url="https://learn.microsoft.com/api/mcp",
    )

    result = runner.run_mcp_tool("microsoft_docs_search", {"query": "az-900"})

    assert result["results"][0]["url"].startswith("https://learn.microsoft.com/")
    assert project_openai_client.responses.calls
    call = project_openai_client.responses.calls[0]
    assert call["tool_choice"]["type"] == "mcp"
    assert call["tool_choice"]["name"] == "microsoft_docs_search"
    assert call["tools"][0]["type"] == "mcp"
    assert call["tools"][0]["project_connection_id"].endswith("/connections/learn-mcp")
    assert call["tools"][0]["server_url"] == "https://learn.microsoft.com/api/mcp"
    assert call["tools"][0]["allowed_tools"] == ["microsoft_docs_search"]


def test_list_mcp_tools_uses_responses_with_project_connection():
    class _Responses:
        def __init__(self) -> None:
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)

            class _Response:
                output = [
                    {
                        "type": "mcp_list_tools",
                        "tools": [
                            {"name": "microsoft_docs_search"},
                            {"name": "microsoft_docs_fetch"},
                        ],
                    }
                ]

            return _Response()

    class _ProjectOpenAIClient:
        def __init__(self):
            self.responses = _Responses()

    class _ProjectsClient:
        def __init__(self, openai_client):
            self._openai_client = openai_client

        def get_openai_client(self):
            return self._openai_client

    project_openai_client = _ProjectOpenAIClient()
    runner = FoundryRunner(
        client=_ProjectsClient(project_openai_client),
        deployment="gpt-test",
        mcp_connection_name="learn-mcp",
        mcp_connection_id="/subscriptions/000/.../connections/learn-mcp",
        mcp_server_url="https://learn.microsoft.com/api/mcp",
    )

    tools = runner.list_mcp_tools()

    assert "microsoft_docs_search" in tools
    assert "microsoft_docs_fetch" in tools
    assert project_openai_client.responses.calls
    call = project_openai_client.responses.calls[0]
    assert call["tool_choice"]["type"] == "mcp"
    assert call["tools"][0]["project_connection_id"].endswith("/connections/learn-mcp")
    assert call["tools"][0]["server_url"] == "https://learn.microsoft.com/api/mcp"


def test_get_foundry_runner_uses_direct_for_classic_endpoint_without_mcp(
    monkeypatch, _clear_foundry_env
):
    monkeypatch.setenv(
        "AZURE_AI_PROJECT_ENDPOINT",
        "https://example.cognitiveservices.azure.com/",
    )
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-test")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")

    sentinel_client = object()
    monkeypatch.setattr(
        foundry_client,
        "_build_direct_azure_openai_client",
        lambda _endpoint: sentinel_client,
    )

    runner = foundry_client.get_foundry_runner()

    assert runner is not None
    assert runner.client is None
    assert runner.openai_client is sentinel_client
