"""Foundry SDK wrapper — isolates all Azure AI Projects SDK calls.

Provides helpers to create agent definitions and run agent prompts.
Supports both online (Foundry endpoint) and offline (stub) modes.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()
_mcp_logger = logging.getLogger("mdt.mcp")


def _short_error(exc: Exception, max_len: int = 240) -> str:
    text = " ".join(str(exc).split())
    if len(text) <= max_len:
        return text
    return f"{text[: max_len - 3].rstrip()}..."


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


def _iter_nodes(value: Any):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _iter_nodes(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_nodes(item)


def _extract_tool_names(payload: Any) -> List[str]:
    """Extract MCP tool names from varied list-tools response shapes."""
    names: List[str] = []
    seen = set()

    for node in _iter_nodes(payload):
        if not isinstance(node, dict):
            continue
        for key in ("name", "tool_name"):
            raw_name = node.get(key)
            if not isinstance(raw_name, str):
                continue
            name = raw_name.strip()
            if not name or name in seen:
                continue
            seen.add(name)
            names.append(name)

    return names


def _looks_like_azure_openai_endpoint(endpoint: str) -> bool:
    host = endpoint.lower()
    return any(
        candidate in host
        for candidate in (
            "openai.azure.com",
            "cognitiveservices.azure.com",
            "api.cognitive.microsoft.com",
            "services.ai.azure.com",
        )
    )


def _as_text(value: Any) -> str:
    """Best-effort extraction of text payloads across SDK response shapes."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [_as_text(item) for item in value]
        return "\n".join([p for p in parts if p.strip()])
    if isinstance(value, dict):
        for key in ("output_text", "text", "content", "value"):
            v = value.get(key)
            if isinstance(v, (str, list, dict)):
                text = _as_text(v)
                if text.strip():
                    return text
        return ""
    for attr in ("output_text", "text", "content", "value"):
        v = getattr(value, attr, None)
        if isinstance(v, (str, list, dict)):
            text = _as_text(v)
            if text.strip():
                return text
    return ""


def _extract_response_text(response: Any) -> str:
    """Normalize model text output from Foundry/OpenAI client responses."""
    text = _as_text(getattr(response, "output_text", None))
    if text.strip():
        return text

    choices = getattr(response, "choices", None)
    if isinstance(choices, list) and choices:
        message = getattr(choices[0], "message", None)
        text = _as_text(message)
        if text.strip():
            return text

    if isinstance(response, dict):
        text = _as_text(response)
        if text.strip():
            return text

    dumped = _to_jsonable(response)
    text = _as_text(dumped)
    if text.strip():
        return text

    return str(response)


def _build_direct_azure_openai_client(endpoint: str) -> Any:
    """Build Azure OpenAI client for direct endpoint usage."""
    from openai import AzureOpenAI

    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")
    api_key = _get_env("AZURE_OPENAI_API_KEY", required=False)
    if api_key:
        return AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
        )

    from azure.identity import DefaultAzureCredential, get_bearer_token_provider

    token_provider = get_bearer_token_provider(
        DefaultAzureCredential(),
        "https://cognitiveservices.azure.com/.default",
    )
    return AzureOpenAI(
        azure_endpoint=endpoint,
        azure_ad_token_provider=token_provider,
        api_version=api_version,
    )


@dataclass
class FoundryRunner:
    """Callable wrapper for model execution with optional MCP tool access."""

    client: Any
    deployment: str
    mcp_connection_name: Optional[str] = None
    openai_client: Optional[Any] = None

    def __call__(self, agent_name: str, system_prompt: str, user_prompt: str) -> str:
        """Execute a single-turn agent call via Foundry."""
        attempts: List[str] = []

        # Legacy Azure AI Projects SDK path (1.x previews).
        try:
            agents = getattr(self.client, "agents", None)
            run = getattr(agents, "run", None)
            if callable(run):
                from azure.ai.projects.models import (
                    SystemMessage,
                    UserMessage,
                )

                response = run(
                    model=self.deployment,
                    messages=[
                        SystemMessage(content=system_prompt),
                        UserMessage(content=user_prompt),
                    ],
                )
                return _extract_response_text(response)
        except Exception as exc:
            attempts.append(f"agents.run: {exc}")

        # Current SDK path via OpenAI-compatible client.
        try:
            openai_client = self.openai_client
            if openai_client is None:
                get_openai_client = getattr(self.client, "get_openai_client", None)
                if callable(get_openai_client):
                    openai_client = get_openai_client()
                    self.openai_client = openai_client

            if openai_client is None:
                raise RuntimeError("No OpenAI-compatible client available")

            response = openai_client.chat.completions.create(
                model=self.deployment,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": f"Agent: {agent_name}\n\n{user_prompt}",
                    },
                ],
            )
            return _extract_response_text(response)
        except Exception as exc:
            attempts.append(f"chat.completions.create: {exc}")

        details = " | ".join(attempts[:3]) if attempts else "unknown runtime error"
        raise RuntimeError(f"Unable to execute Foundry call. {details}")

    def run_mcp_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute MCP tool call if the SDK exposes a compatible surface.

        Raises RuntimeError when MCP capability is unavailable in this SDK/runtime.
        """
        if self.client is None:
            raise RuntimeError(
                "MCP tool execution requires an Azure AI Projects client runtime."
            )

        # We intentionally probe a small set of candidate SDK surfaces and fail
        # clearly when not available so callers can fall back safely.
        attempts = []
        _mcp_logger.info(
            "mcp_tool_call_start",
            extra={
                "event": "mcp_tool_call_start",
                "tool_name": tool_name,
                "connection_name_configured": bool(self.mcp_connection_name),
                "argument_keys": (
                    sorted(arguments.keys()) if isinstance(arguments, dict) else []
                ),
            },
        )

        def _invoke(surface: str, callable_obj, kwargs: Dict[str, Any]):
            started = perf_counter()
            try:
                result = callable_obj(**kwargs)
                _mcp_logger.info(
                    "mcp_tool_call_success",
                    extra={
                        "event": "mcp_tool_call_success",
                        "tool_name": tool_name,
                        "sdk_surface": surface,
                        "latency_ms": round((perf_counter() - started) * 1000, 2),
                        "connection_name_used": "connection_name" in kwargs,
                    },
                )
                return result
            except Exception as exc:
                attempts.append(f"{callable_obj}: {exc}")
                _mcp_logger.warning(
                    "mcp_tool_call_failure",
                    extra={
                        "event": "mcp_tool_call_failure",
                        "tool_name": tool_name,
                        "sdk_surface": surface,
                        "latency_ms": round((perf_counter() - started) * 1000, 2),
                        "connection_name_used": "connection_name" in kwargs,
                        "error": _short_error(exc),
                    },
                )
                return None

        # Candidate 1: client.mcp.call_tool(...)
        mcp = getattr(self.client, "mcp", None)
        if mcp is not None:
            call_tool = getattr(mcp, "call_tool", None)
            if callable(call_tool):
                result = _invoke(
                    "mcp",
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
                    "tools",
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
                result = _invoke("connections", invoke_tool, kwargs)
                if result is not None:
                    return _to_jsonable(result)

        reason = (
            "MCP tool execution is not available from this Foundry client runtime."
        )
        if attempts:
            reason = f"{reason} Attempts: {' | '.join(attempts[:3])}"
        _mcp_logger.error(
            "mcp_tool_call_unavailable",
            extra={
                "event": "mcp_tool_call_unavailable",
                "tool_name": tool_name,
                "attempts_count": len(attempts),
                "reason": reason,
            },
        )
        raise RuntimeError(reason)

    def list_mcp_tools(self) -> List[str]:
        """Return available MCP tool names when runtime exposes discovery."""
        if self.client is None:
            raise RuntimeError(
                "MCP tool discovery requires an Azure AI Projects client runtime."
            )

        attempts = []
        _mcp_logger.info(
            "mcp_list_tools_start",
            extra={
                "event": "mcp_list_tools_start",
                "connection_name_configured": bool(self.mcp_connection_name),
            },
        )

        def _invoke(surface: str, callable_obj, kwargs: Dict[str, Any]):
            started = perf_counter()
            try:
                result = callable_obj(**kwargs)
                _mcp_logger.info(
                    "mcp_list_tools_call_success",
                    extra={
                        "event": "mcp_list_tools_call_success",
                        "sdk_surface": surface,
                        "latency_ms": round((perf_counter() - started) * 1000, 2),
                        "connection_name_used": "connection_name" in kwargs,
                    },
                )
                return result
            except Exception as exc:
                attempts.append(f"{callable_obj}: {exc}")
                _mcp_logger.warning(
                    "mcp_list_tools_call_failure",
                    extra={
                        "event": "mcp_list_tools_call_failure",
                        "sdk_surface": surface,
                        "latency_ms": round((perf_counter() - started) * 1000, 2),
                        "connection_name_used": "connection_name" in kwargs,
                        "error": _short_error(exc),
                    },
                )
                return None

        # Candidate 1: client.mcp.list_tools(...)
        mcp = getattr(self.client, "mcp", None)
        if mcp is not None:
            list_tools = getattr(mcp, "list_tools", None)
            if callable(list_tools):
                kwargs: Dict[str, Any] = {}
                result = _invoke("mcp", list_tools, kwargs)
                if result is not None:
                    names = _extract_tool_names(_to_jsonable(result))
                    if names:
                        _mcp_logger.info(
                            "mcp_list_tools_success",
                            extra={
                                "event": "mcp_list_tools_success",
                                "sdk_surface": "mcp",
                                "tools_count": len(names),
                            },
                        )
                        return names

        # Candidate 2: client.tools.list(...)
        tools = getattr(self.client, "tools", None)
        if tools is not None:
            list_tools = getattr(tools, "list", None)
            if callable(list_tools):
                kwargs = {}
                result = _invoke("tools", list_tools, kwargs)
                if result is not None:
                    names = _extract_tool_names(_to_jsonable(result))
                    if names:
                        _mcp_logger.info(
                            "mcp_list_tools_success",
                            extra={
                                "event": "mcp_list_tools_success",
                                "sdk_surface": "tools",
                                "tools_count": len(names),
                            },
                        )
                        return names

        # Candidate 3: client.connections.list_tools(...)
        connections = getattr(self.client, "connections", None)
        if connections is not None:
            list_tools = getattr(connections, "list_tools", None)
            if callable(list_tools):
                kwargs: Dict[str, Any] = {}
                if self.mcp_connection_name:
                    kwargs["connection_name"] = self.mcp_connection_name
                result = _invoke("connections", list_tools, kwargs)
                if result is not None:
                    names = _extract_tool_names(_to_jsonable(result))
                    if names:
                        _mcp_logger.info(
                            "mcp_list_tools_success",
                            extra={
                                "event": "mcp_list_tools_success",
                                "sdk_surface": "connections",
                                "tools_count": len(names),
                                "connection_name_used": "connection_name" in kwargs,
                            },
                        )
                        return names

        reason = "MCP tool discovery is not available from this Foundry client runtime."
        if attempts:
            reason = f"{reason} Attempts: {' | '.join(attempts[:3])}"
        _mcp_logger.error(
            "mcp_list_tools_unavailable",
            extra={
                "event": "mcp_list_tools_unavailable",
                "attempts_count": len(attempts),
                "reason": reason,
            },
        )
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

    init_errors: List[str] = []

    # If this is a direct Azure OpenAI endpoint and a key is provided,
    # prefer key auth to avoid VM identity requirements.
    if _looks_like_azure_openai_endpoint(endpoint) and _get_env(
        "AZURE_OPENAI_API_KEY", required=False
    ):
        try:
            openai_client = _build_direct_azure_openai_client(endpoint)
            return FoundryRunner(
                client=None,
                deployment=deployment,
                mcp_connection_name=mcp_connection_name,
                openai_client=openai_client,
            )
        except Exception as exc:
            init_errors.append(f"AzureOpenAI key auth init failed: {exc}")

    # Preferred: Azure AI Projects SDK (supports MCP when available).
    try:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential

        client = AIProjectClient(
            endpoint=endpoint,
            credential=DefaultAzureCredential(),
        )
        openai_client = None
        get_openai_client = getattr(client, "get_openai_client", None)
        if callable(get_openai_client):
            try:
                openai_client = get_openai_client()
            except Exception as exc:
                init_errors.append(f"projects.get_openai_client failed: {exc}")

        return FoundryRunner(
            client=client,
            deployment=deployment,
            mcp_connection_name=mcp_connection_name,
            openai_client=openai_client,
        )
    except Exception as exc:
        init_errors.append(f"AIProjectClient init failed: {exc}")

    # Fallback: direct Azure OpenAI endpoint.
    if _looks_like_azure_openai_endpoint(endpoint):
        try:
            openai_client = _build_direct_azure_openai_client(endpoint)
            return FoundryRunner(
                client=None,
                deployment=deployment,
                mcp_connection_name=mcp_connection_name,
                openai_client=openai_client,
            )
        except Exception as exc:
            init_errors.append(f"AzureOpenAI fallback init failed: {exc}")

    from .util.console import console

    details = " | ".join(init_errors[:3]) if init_errors else "unknown error"
    console.print(f"[yellow]⚠ Could not initialize Foundry client: {details}[/yellow]")
    console.print("[yellow]Running in offline mode (stub outputs).[/yellow]")
    return None
