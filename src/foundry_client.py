"""Foundry SDK wrapper — isolates all Azure AI Projects SDK calls.

Provides helpers to create agent definitions and run agent prompts.
Supports both online (Foundry endpoint) and offline (stub) modes.
"""

from __future__ import annotations

import os
from typing import Optional

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


def get_foundry_runner():
    """Return a callable ``(agent_name, system_prompt, user_prompt) -> str``.

    This connects to Azure AI Foundry using the Projects SDK.
    If the SDK or credentials are unavailable, returns None (caller should
    fall back to offline mode).
    """
    try:
        endpoint = _get_env("AZURE_AI_PROJECT_ENDPOINT")
        deployment = _get_env("AZURE_AI_MODEL_DEPLOYMENT_NAME")
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
        console.print(f"[yellow]⚠ Could not initialise Foundry client: {exc}[/yellow]")
        console.print("[yellow]Running in offline mode (stub outputs).[/yellow]")
        return None

    def _run(agent_name: str, system_prompt: str, user_prompt: str) -> str:
        """Execute a single-turn agent call via Foundry."""
        from azure.ai.projects.models import (
            UserMessage,
            SystemMessage,
        )

        response = client.agents.run(
            model=deployment,
            messages=[
                SystemMessage(content=system_prompt),
                UserMessage(content=user_prompt),
            ],
        )
        # Extract text from response
        return response.choices[0].message.content

    return _run
