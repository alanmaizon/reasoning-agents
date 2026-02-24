"""Foundry SDK wrapper — isolates all Azure AI Projects SDK calls.

Provides helpers to create agent definitions and run agent prompts.
Supports both online (Foundry endpoint) and offline (stub) modes.
"""

from __future__ import annotations

import logging
import os
import json
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

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


def _pick_first_string(node: Any, keys: List[str]) -> Optional[str]:
    if not isinstance(node, dict):
        return None
    for key in keys:
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_mcp_connection_config(
    raw_connection: Any,
    *,
    fallback_name: Optional[str],
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Extract connection id/target/label from varied SDK connection shapes."""
    payload = _to_jsonable(raw_connection)
    if not isinstance(payload, dict):
        return None, None, fallback_name

    properties = payload.get("properties")
    if not isinstance(properties, dict):
        properties = {}

    connection_id = (
        _pick_first_string(payload, ["id", "connection_id", "resource_id"])
        or _pick_first_string(properties, ["id", "connection_id", "resource_id"])
    )
    server_url = (
        _pick_first_string(payload, ["target", "endpoint", "url", "server_url"])
        or _pick_first_string(properties, ["target", "endpoint", "url", "server_url"])
    )
    server_label = (
        _pick_first_string(payload, ["name", "connection_name"])
        or _pick_first_string(properties, ["name", "connection_name"])
        or fallback_name
    )
    return connection_id, server_url, server_label


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


def _looks_like_foundry_project_endpoint(endpoint: str) -> bool:
    lower = endpoint.lower()
    return "services.ai.azure.com" in lower or "/api/projects/" in lower


def _derive_openai_endpoint_from_project_endpoint(project_endpoint: str) -> Optional[str]:
    """Derive direct Azure OpenAI endpoint from a Foundry project endpoint host."""
    parsed = urlparse(project_endpoint)
    host = parsed.netloc.strip().lower()
    suffix = ".services.ai.azure.com"
    if not host.endswith(suffix):
        return None
    resource_name = host[: -len(suffix)].strip()
    if not resource_name:
        return None
    return f"https://{resource_name}.openai.azure.com"


def _resolve_direct_model_endpoint(endpoint: str, endpoint_is_project: bool) -> Optional[str]:
    """Resolve endpoint to use for direct model inference calls."""
    explicit = _get_env("AZURE_OPENAI_ENDPOINT", required=False)
    if explicit:
        return explicit
    if endpoint_is_project:
        return _derive_openai_endpoint_from_project_endpoint(endpoint)
    if _looks_like_azure_openai_endpoint(endpoint):
        return endpoint
    return None


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
    mcp_connection_id: Optional[str] = None
    mcp_server_url: Optional[str] = None
    mcp_server_label: Optional[str] = None
    openai_client: Optional[Any] = None
    project_openai_client: Optional[Any] = None

    def _get_project_openai_client(self) -> Optional[Any]:
        if self.project_openai_client is not None:
            return self.project_openai_client
        if self.client is None:
            return None
        get_openai_client = getattr(self.client, "get_openai_client", None)
        if not callable(get_openai_client):
            return None
        try:
            self.project_openai_client = get_openai_client()
        except Exception:
            return None
        return self.project_openai_client

    def _build_mcp_tool_payload(
        self,
        *,
        allowed_tools: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        server_label = (
            self.mcp_server_label
            or self.mcp_connection_name
            or "project-mcp"
        )
        tool: Dict[str, Any] = {
            "type": "mcp",
            "server_label": server_label,
            "require_approval": "never",
        }
        if self.mcp_server_url:
            tool["server_url"] = self.mcp_server_url
        if self.mcp_connection_id:
            tool["project_connection_id"] = self.mcp_connection_id
        if allowed_tools:
            tool["allowed_tools"] = allowed_tools
        return tool

    def _extract_mcp_call_output(
        self,
        response: Any,
        *,
        tool_name: str,
    ) -> Optional[Dict[str, Any]]:
        output_items = _to_jsonable(getattr(response, "output", None))
        if not isinstance(output_items, list):
            return None
        for item in output_items:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "mcp_call":
                continue
            item_name = item.get("name") or item.get("tool_name")
            if item_name and item_name != tool_name:
                continue
            raw_error = item.get("error")
            if raw_error:
                raise RuntimeError(f"MCP call returned error: {raw_error}")
            raw_output = item.get("output")
            if isinstance(raw_output, dict):
                return raw_output
            if isinstance(raw_output, str):
                stripped = raw_output.strip()
                if not stripped:
                    return {"output": raw_output}
                try:
                    parsed = json.loads(stripped)
                    if isinstance(parsed, dict):
                        return parsed
                    return {"result": parsed}
                except Exception:
                    return {"output": raw_output}
            if raw_output is not None:
                return {"result": raw_output}
        return None

    def _run_mcp_via_responses(
        self,
        *,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        openai_client = self._get_project_openai_client()
        if openai_client is None:
            return None
        responses = getattr(openai_client, "responses", None)
        create = getattr(responses, "create", None)
        if not callable(create):
            return None
        if not (self.mcp_server_url or self.mcp_connection_id):
            return None

        tool_payload = self._build_mcp_tool_payload(allowed_tools=[tool_name])
        server_label = tool_payload["server_label"]
        prompt = (
            f"Call MCP tool '{tool_name}' exactly once using the JSON arguments below.\n"
            f"{json.dumps(arguments, ensure_ascii=False)}"
        )
        response = create(
            model=self.deployment,
            input=prompt,
            tools=[tool_payload],
            tool_choice={
                "type": "mcp",
                "server_label": server_label,
                "name": tool_name,
            },
            max_output_tokens=256,
        )
        return self._extract_mcp_call_output(response, tool_name=tool_name)

    def _list_mcp_tools_via_responses(self) -> Optional[List[str]]:
        openai_client = self._get_project_openai_client()
        if openai_client is None:
            return None
        responses = getattr(openai_client, "responses", None)
        create = getattr(responses, "create", None)
        if not callable(create):
            return None
        if not (self.mcp_server_url or self.mcp_connection_id):
            return None

        tool_payload = self._build_mcp_tool_payload()
        server_label = tool_payload["server_label"]
        response = create(
            model=self.deployment,
            input="List available tools from this MCP server.",
            tools=[tool_payload],
            tool_choice={"type": "mcp", "server_label": server_label},
            max_output_tokens=64,
        )
        output_items = _to_jsonable(getattr(response, "output", None))
        if not isinstance(output_items, list):
            return None

        names: List[str] = []
        seen = set()
        for item in output_items:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "mcp_list_tools":
                continue
            for name in _extract_tool_names(item):
                if name in seen:
                    continue
                seen.add(name)
                names.append(name)
        return names or None

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

        # Candidate 4: OpenAI Responses API MCP tool call using project connection.
        started = perf_counter()
        try:
            result = self._run_mcp_via_responses(
                tool_name=tool_name,
                arguments=arguments,
            )
            if result is not None:
                _mcp_logger.info(
                    "mcp_tool_call_success",
                    extra={
                        "event": "mcp_tool_call_success",
                        "tool_name": tool_name,
                        "sdk_surface": "responses_mcp",
                        "latency_ms": round((perf_counter() - started) * 1000, 2),
                        "connection_name_used": bool(self.mcp_connection_name),
                    },
                )
                return _to_jsonable(result)
        except Exception as exc:
            attempts.append(f"responses.mcp: {exc}")
            _mcp_logger.warning(
                "mcp_tool_call_failure",
                extra={
                    "event": "mcp_tool_call_failure",
                    "tool_name": tool_name,
                    "sdk_surface": "responses_mcp",
                    "latency_ms": round((perf_counter() - started) * 1000, 2),
                    "connection_name_used": bool(self.mcp_connection_name),
                    "error": _short_error(exc),
                },
            )

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

        # Candidate 4: OpenAI Responses API MCP list-tools using project connection.
        started = perf_counter()
        try:
            names = self._list_mcp_tools_via_responses()
            if names:
                _mcp_logger.info(
                    "mcp_list_tools_success",
                    extra={
                        "event": "mcp_list_tools_success",
                        "sdk_surface": "responses_mcp",
                        "tools_count": len(names),
                        "connection_name_used": bool(self.mcp_connection_name),
                        "latency_ms": round((perf_counter() - started) * 1000, 2),
                    },
                )
                return names
        except Exception as exc:
            attempts.append(f"responses.mcp: {exc}")
            _mcp_logger.warning(
                "mcp_list_tools_call_failure",
                extra={
                    "event": "mcp_list_tools_call_failure",
                    "sdk_surface": "responses_mcp",
                    "latency_ms": round((perf_counter() - started) * 1000, 2),
                    "connection_name_used": bool(self.mcp_connection_name),
                    "error": _short_error(exc),
                },
            )

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
        mcp_server_url_env = _get_env("MCP_SERVER_URL", required=False)
    except EnvironmentError as exc:
        from .util.console import console
        console.print(f"[yellow]⚠ {exc}[/yellow]")
        console.print("[yellow]Running in offline mode (stub outputs).[/yellow]")
        return None

    init_errors: List[str] = []
    endpoint_is_project = _looks_like_foundry_project_endpoint(endpoint)
    direct_endpoint_supported = _looks_like_azure_openai_endpoint(endpoint)
    mcp_requested = bool(mcp_connection_name)
    prefer_projects = endpoint_is_project or mcp_requested
    api_key_configured = bool(_get_env("AZURE_OPENAI_API_KEY", required=False))

    _mcp_logger.info(
        "foundry_runner_init",
        extra={
            "event": "foundry_runner_init",
            "endpoint_is_project": endpoint_is_project,
            "direct_endpoint_supported": direct_endpoint_supported,
            "mcp_requested": mcp_requested,
            "prefer_projects": prefer_projects,
            "api_key_configured": api_key_configured,
        },
    )

    def _init_projects_runner() -> Optional[FoundryRunner]:
        try:
            from azure.ai.projects import AIProjectClient
            from azure.identity import DefaultAzureCredential

            client = AIProjectClient(
                endpoint=endpoint,
                credential=DefaultAzureCredential(),
            )
            openai_client = None
            model_runtime = "ai_projects_openai_client"
            mcp_connection_id: Optional[str] = None
            mcp_server_url: Optional[str] = mcp_server_url_env
            mcp_server_label: Optional[str] = mcp_connection_name

            # For project endpoints, prefer direct Azure OpenAI inference when possible.
            # This keeps MCP/tool capability via project client while using stable model
            # deployment routing for planner/examiner/coach/grounding model calls.
            direct_model_endpoint = _resolve_direct_model_endpoint(
                endpoint=endpoint,
                endpoint_is_project=endpoint_is_project,
            )
            if direct_model_endpoint and (
                api_key_configured or not endpoint_is_project
            ):
                try:
                    openai_client = _build_direct_azure_openai_client(direct_model_endpoint)
                    model_runtime = "azure_openai_direct"
                except Exception as exc:
                    init_errors.append(f"projects.direct_model_client_init failed: {exc}")

            if openai_client is None:
                get_openai_client = getattr(client, "get_openai_client", None)
                if callable(get_openai_client):
                    try:
                        openai_client = get_openai_client()
                    except Exception as exc:
                        init_errors.append(f"projects.get_openai_client failed: {exc}")

            if mcp_requested and mcp_connection_name:
                connections = getattr(client, "connections", None)
                get_connection = getattr(connections, "get", None)
                if callable(get_connection):
                    connection = None
                    for kwargs in (
                        {"name": mcp_connection_name, "include_credentials": True},
                        {"name": mcp_connection_name},
                        {"connection_name": mcp_connection_name},
                    ):
                        try:
                            connection = get_connection(**kwargs)
                            break
                        except TypeError:
                            continue
                        except Exception as exc:
                            init_errors.append(f"projects.connections.get failed: {exc}")
                            break
                    if connection is not None:
                        (
                            mcp_connection_id,
                            mcp_connection_target,
                            mcp_connection_label,
                        ) = _extract_mcp_connection_config(
                            connection,
                            fallback_name=mcp_connection_name,
                        )
                        if mcp_connection_target:
                            mcp_server_url = mcp_connection_target
                        if mcp_connection_label:
                            mcp_server_label = mcp_connection_label
                        _mcp_logger.info(
                            "foundry_mcp_connection_resolved",
                            extra={
                                "event": "foundry_mcp_connection_resolved",
                                "connection_name": mcp_connection_name,
                                "connection_id_resolved": bool(mcp_connection_id),
                                "server_url_resolved": bool(mcp_server_url),
                            },
                        )
                    else:
                        _mcp_logger.warning(
                            "foundry_mcp_connection_unresolved",
                            extra={
                                "event": "foundry_mcp_connection_unresolved",
                                "connection_name": mcp_connection_name,
                                "server_url_configured": bool(mcp_server_url),
                            },
                        )
                else:
                    _mcp_logger.warning(
                        "foundry_mcp_connection_lookup_unsupported",
                        extra={
                            "event": "foundry_mcp_connection_lookup_unsupported",
                            "connection_name": mcp_connection_name,
                        },
                    )

            _mcp_logger.info(
                "foundry_runner_selected",
                extra={
                    "event": "foundry_runner_selected",
                    "runtime": "ai_projects",
                    "model_runtime": model_runtime,
                    "mcp_connection_configured": mcp_requested,
                    "endpoint_is_project": endpoint_is_project,
                    "direct_model_endpoint_used": bool(
                        openai_client is not None and model_runtime == "azure_openai_direct"
                    ),
                },
            )
            return FoundryRunner(
                client=client,
                deployment=deployment,
                mcp_connection_name=mcp_connection_name,
                mcp_connection_id=mcp_connection_id,
                mcp_server_url=mcp_server_url,
                mcp_server_label=mcp_server_label,
                openai_client=openai_client,
            )
        except Exception as exc:
            init_errors.append(f"AIProjectClient init failed: {exc}")
            return None

    def _init_direct_runner(error_prefix: str) -> Optional[FoundryRunner]:
        try:
            openai_client = _build_direct_azure_openai_client(endpoint)
            _mcp_logger.info(
                "foundry_runner_selected",
                extra={
                    "event": "foundry_runner_selected",
                    "runtime": "azure_openai_direct",
                    "mcp_connection_configured": mcp_requested,
                    "endpoint_is_project": endpoint_is_project,
                },
            )
            return FoundryRunner(
                client=None,
                deployment=deployment,
                mcp_connection_name=mcp_connection_name,
                openai_client=openai_client,
            )
        except Exception as exc:
            init_errors.append(f"{error_prefix}: {exc}")
            return None

    # For project endpoints (or explicit MCP config), prefer the Projects client.
    if prefer_projects:
        projects_runner = _init_projects_runner()
        if projects_runner is not None:
            return projects_runner
        if mcp_requested:
            _mcp_logger.warning(
                "foundry_runner_mcp_degraded",
                extra={
                    "event": "foundry_runner_mcp_degraded",
                    "reason": "ai_projects_init_failed",
                },
            )

    # For classic direct endpoints, key-auth can be fastest when MCP isn't requested.
    if not prefer_projects and direct_endpoint_supported and api_key_configured:
        direct_runner = _init_direct_runner("AzureOpenAI key auth init failed")
        if direct_runner is not None:
            return direct_runner

    # Keep Projects SDK as next fallback for managed identity and richer runtime.
    if not prefer_projects:
        projects_runner = _init_projects_runner()
        if projects_runner is not None:
            return projects_runner

    # Fallback: direct Azure OpenAI endpoint.
    if direct_endpoint_supported:
        direct_runner = _init_direct_runner("AzureOpenAI fallback init failed")
        if direct_runner is not None:
            return direct_runner

    from .util.console import console

    details = " | ".join(init_errors[:3]) if init_errors else "unknown error"
    console.print(f"[yellow]⚠ Could not initialize Foundry client: {details}[/yellow]")
    console.print("[yellow]Running in offline mode (stub outputs).[/yellow]")
    return None
