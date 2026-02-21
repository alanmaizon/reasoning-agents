"""Foundry SDK wrapper — isolates all Azure AI Projects SDK calls.

Provides helpers to create agent definitions and run agent prompts.
Supports both online (Foundry endpoint) and offline (stub) modes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from dotenv import load_dotenv

load_dotenv()


def _get_env(name: str, required: bool = True) -> Optional[str]:
    val = os.environ.get(name)
    if required and not val:
        raise EnvironmentError(
            f"Missing required environment variable: {name}. "
            f"Set it in .env or export it. See .env.example."
        )
    return val


def _to_jsonable(value: Any) -> Any:
    """Best-effort conversion of SDK objects to plain Python structures."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _to_jsonable(model_dump())
        except Exception:
            pass
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict):
        try:
            return _to_jsonable(as_dict())
        except Exception:
            pass
    data = getattr(value, "__dict__", None)
    if isinstance(data, dict):
        return _to_jsonable(data)
    return str(value)


@dataclass
class FoundryRunner:
    """Callable wrapper for model execution with optional MCP tool access."""

    client: Any
    deployment: str
    mcp_connection_name: Optional[str] = None

    def __call__(self, agent_name: str, system_prompt: str, user_prompt: str) -> str:
        """Execute a single-turn agent call via Foundry."""
        from azure.ai.projects.models import (
            UserMessage,
            SystemMessage,
        )

        response = self.client.agents.run(
            model=self.deployment,
            messages=[
                SystemMessage(content=system_prompt),
                UserMessage(content=user_prompt),
            ],
        )
        # Extract text from response
        return response.choices[0].message.content

    def run_mcp_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute MCP tool call if the SDK exposes a compatible surface.

        Raises RuntimeError when MCP capability is unavailable in this SDK/runtime.
        """
        # We intentionally probe a small set of candidate SDK surfaces and fail
        # clearly when not available so callers can fall back safely.
        attempts = []

        def _invoke(callable_obj, kwargs: Dict[str, Any]):
            try:
                return callable_obj(**kwargs)
            except Exception as exc:
                attempts.append(f"{callable_obj}: {exc}")
                return None

        # Candidate 1: client.mcp.call_tool(...)
        mcp = getattr(self.client, "mcp", None)
        if mcp is not None:
            call_tool = getattr(mcp, "call_tool", None)
            if callable(call_tool):
                result = _invoke(
                    call_tool,
                    {
                        "tool_name": tool_name,
                        "arguments": arguments,
                    },
                )
                if result is not None:
                    return _to_jsonable(result)

        # Candidate 2: client.tools.invoke(...)
        tools = getattr(self.client, "tools", None)
        if tools is not None:
            invoke = getattr(tools, "invoke", None)
            if callable(invoke):
                result = _invoke(
                    invoke,
                    {
                        "tool_name": tool_name,
                        "arguments": arguments,
                    },
                )
                if result is not None:
                    return _to_jsonable(result)

        # Candidate 3: client.connections.invoke_tool(...)
        connections = getattr(self.client, "connections", None)
        if connections is not None:
            invoke_tool = getattr(connections, "invoke_tool", None)
            if callable(invoke_tool):
                kwargs: Dict[str, Any] = {
                    "tool_name": tool_name,
                    "arguments": arguments,
                }
                if self.mcp_connection_name:
                    kwargs["connection_name"] = self.mcp_connection_name
                result = _invoke(invoke_tool, kwargs)
                if result is not None:
                    return _to_jsonable(result)

        reason = (
            "MCP tool execution is not available from this Foundry client runtime."
        )
        if attempts:
            reason = f"{reason} Attempts: {' | '.join(attempts[:3])}"
        raise RuntimeError(reason)


def get_foundry_runner():
    """Return a callable ``(agent_name, system_prompt, user_prompt) -> str``.

    This connects to Azure AI Foundry using the Projects SDK.
    If the SDK or credentials are unavailable, returns None (caller should
    fall back to offline mode).
    """
    try:
        endpoint = _get_env("AZURE_AI_PROJECT_ENDPOINT")
        deployment = _get_env("AZURE_AI_MODEL_DEPLOYMENT_NAME")
        mcp_connection_name = _get_env("MCP_PROJECT_CONNECTION_NAME", required=False)
    except EnvironmentError as exc:
        from .util.console import console
        console.print(f"[yellow]⚠ {exc}[/yellow]")
        console.print("[yellow]Running in offline mode (stub outputs).[/yellow]")
        return None

    try:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential

        client = AIProjectClient(
            endpoint=endpoint,
            credential=DefaultAzureCredential(),
        )
    except Exception as exc:
        from .util.console import console
        console.print(f"[yellow]⚠ Could not initialize Foundry client: {exc}[/yellow]")
        console.print("[yellow]Running in offline mode (stub outputs).[/yellow]")
        return None

    return FoundryRunner(
        client=client,
        deployment=deployment,
        mcp_connection_name=mcp_connection_name,
    )
