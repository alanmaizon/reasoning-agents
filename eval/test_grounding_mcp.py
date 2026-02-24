"""Grounding MCP integration tests with stub tool runner."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents.grounding_verifier import _supports_tool_runner, run_grounding_verifier
from src.foundry_client import FoundryRunner
from src.models.schemas import DiagnosisResult, Question


class CodeSampleOnlyRunner:
    """Stub runner that exposes only code-sample search via MCP discovery."""

    def __init__(self) -> None:
        self.tool_calls = []

    def list_mcp_tools(self):
        return ["microsoft_code_sample_search"]

    def run_mcp_tool(self, tool_name, arguments):
        self.tool_calls.append((tool_name, arguments))
        if tool_name != "microsoft_code_sample_search":
            raise RuntimeError(f"unexpected tool call: {tool_name}")
        return {
            "results": [
                {
                    "title": "Deploy a Linux VM with Bicep",
                    "url": "https://learn.microsoft.com/en-us/azure/virtual-machines/linux/quick-create-bicep",
                    "snippet": "Use a Bicep template to deploy a Linux virtual machine.",
                }
            ]
        }

    def __call__(self, agent_name: str, system_prompt: str, user_prompt: str) -> str:
        # Return deterministic JSON the grounding agent expects.
        return json.dumps(
            {
                "question_id": "q1",
                "explanation": "Bicep can provision Azure resources declaratively.",
                "citations": [
                    {
                        "title": "Deploy a Linux VM with Bicep",
                        "url": "https://learn.microsoft.com/en-us/azure/virtual-machines/linux/quick-create-bicep",
                        "snippet": "Use a Bicep template to deploy a Linux virtual machine.",
                    }
                ],
            }
        )


class ThrottledMCPRunner:
    """Stub runner that always returns MCP 429 to validate fail-fast behavior."""

    def __init__(self) -> None:
        self.tool_calls = 0
        self.list_calls = 0

    def list_mcp_tools(self):
        self.list_calls += 1
        return ["microsoft_docs_search", "microsoft_code_sample_search", "microsoft_docs_fetch"]

    def run_mcp_tool(self, tool_name, arguments):
        self.tool_calls += 1
        raise RuntimeError(
            "Error code: 429 - {'error': {'message': 'Too Many Requests', 'code': 'too_many_requests'}}"
        )

    def __call__(self, agent_name: str, system_prompt: str, user_prompt: str) -> str:
        # Grounding still returns a valid model payload after MCP throttling.
        return json.dumps(
            {
                "question_id": "q1",
                "explanation": "Use Microsoft Learn guidance for shared responsibility.",
                "citations": [
                    {
                        "title": "Shared responsibility in the cloud",
                        "url": "https://learn.microsoft.com/en-us/azure/security/fundamentals/shared-responsibility",
                        "snippet": "Responsibilities vary by service type: SaaS, PaaS, IaaS.",
                    }
                ],
            }
        )


def test_grounding_uses_code_sample_search_when_discovered():
    runner = CodeSampleOnlyRunner()
    question = Question(
        id="q1",
        domain="Azure Architecture",
        stem="Which IaC option is supported for Azure deployments?",
        choices=["A", "B", "C", "D"],
        answer_key=0,
        rationale_draft="Bicep and ARM templates are supported.",
    )
    diagnosis = DiagnosisResult(
        id="q1",
        correct=False,
        misconception_id="SERVICE_SCOPE",
        why="Student mixed up deployment models.",
        confidence=0.9,
    )

    result = run_grounding_verifier(
        question=question,
        diagnosis_result=diagnosis,
        offline=False,
        foundry_run=runner,
    )

    assert result.question_id == "q1"
    assert result.citations
    assert result.citations[0].url.startswith("https://learn.microsoft.com/")
    called_tools = [name for name, _ in runner.tool_calls]
    assert "microsoft_code_sample_search" in called_tools
    assert "microsoft_docs_search" not in called_tools


def test_grounding_mcp_rate_limit_fails_fast():
    runner = ThrottledMCPRunner()
    question = Question(
        id="q1",
        domain="Cloud Concepts",
        stem="Which statement best describes shared responsibility in Azure?",
        choices=["A", "B", "C", "D"],
        answer_key=0,
        rationale_draft="Shared responsibility varies by cloud service model.",
    )
    diagnosis = DiagnosisResult(
        id="q1",
        correct=False,
        misconception_id="SERVICE_SCOPE",
        why="Student assumed provider owns all security tasks.",
        confidence=0.9,
    )

    result = run_grounding_verifier(
        question=question,
        diagnosis_result=diagnosis,
        offline=False,
        foundry_run=runner,
    )

    assert result.question_id == "q1"
    assert result.citations
    # Fail-fast on 429 should avoid retry fanout across argument shapes/queries.
    assert runner.tool_calls == 1
    assert runner.list_calls == 1

    # A second grounding call with the same runner should skip MCP entirely while
    # cooldown is active, avoiding additional throttled calls.
    second = run_grounding_verifier(
        question=question,
        diagnosis_result=diagnosis,
        offline=False,
        foundry_run=runner,
    )
    assert second.question_id == "q1"
    assert second.citations
    assert runner.tool_calls == 1
    assert runner.list_calls == 1


def test_direct_openai_runner_not_treated_as_mcp_capable():
    runner = FoundryRunner(
        client=None,
        deployment="test-model",
        openai_client=object(),
    )
    assert _supports_tool_runner(runner) is False
