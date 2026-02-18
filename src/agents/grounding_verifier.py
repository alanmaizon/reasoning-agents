"""GroundingVerifierAgent — grounds explanations with Microsoft Learn citations."""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

from ..models.schemas import (
    Citation,
    DiagnosisResult,
    GroundedExplanation,
    Question,
)
from ..orchestration.cache import cache_get, cache_put
from ..orchestration.tool_policy import is_tool_allowed
from ..util.jsonio import extract_json


GROUNDING_SYSTEM_PROMPT = """\
You are the GroundingVerifierAgent for an AZ-900 tutor.
For a given question the student got wrong, produce a grounded explanation with
citations from Microsoft Learn documentation.
CRITICAL RULES:
- Every claim MUST have a citation with title, url, and snippet (<=20 words).
- If you cannot find a citation, respond with explanation =
  "Insufficient evidence — please narrow your query." and still provide at least
  one placeholder citation.
- Output ONLY valid JSON:
{
  "question_id": "<id>",
  "explanation": "<grounded explanation>",
  "citations": [
    {"title": "<doc title>", "url": "<learn url>", "snippet": "<<=20 words>"}
  ]
}
"""

# ── Stub citations for offline mode ─────────────────────────────────
_STUB_CITATIONS = [
    Citation(
        title="Shared responsibility in the cloud",
        url="https://learn.microsoft.com/en-us/azure/security/fundamentals/shared-responsibility",
        snippet="Responsibilities vary by service type: SaaS, PaaS, IaaS.",
    ),
    Citation(
        title="Azure regions and availability zones",
        url="https://learn.microsoft.com/en-us/azure/reliability/availability-zones-overview",
        snippet="Availability Zones are unique physical locations within a region.",
    ),
    Citation(
        title="What is Microsoft Entra ID?",
        url="https://learn.microsoft.com/en-us/entra/fundamentals/whatis",
        snippet="Cloud-based identity and access management service.",
    ),
]


def _offline_ground(question: Question, diag: Optional[DiagnosisResult]) -> GroundedExplanation:
    """Return a deterministic stub grounded explanation."""
    domain_citations = {
        "Cloud Concepts": _STUB_CITATIONS[0],
        "Azure Architecture": _STUB_CITATIONS[1],
        "Security": _STUB_CITATIONS[2],
    }
    cite = domain_citations.get(question.domain, _STUB_CITATIONS[0])
    explanation = (
        f"The correct answer is choice {question.answer_key + 1}. "
        f"{question.rationale_draft}"
    )
    return GroundedExplanation(
        question_id=question.id,
        explanation=explanation,
        citations=[cite],
    )


def run_grounding_verifier(
    question: Question,
    diagnosis_result: Optional[DiagnosisResult] = None,
    offline: bool = False,
    foundry_run: Optional[Callable[..., str]] = None,
) -> GroundedExplanation:
    if offline or foundry_run is None:
        return _offline_ground(question, diagnosis_result)

    diag_json = diagnosis_result.model_dump() if diagnosis_result else {}
    prompt = (
        f"Question:\n{json.dumps(question.model_dump(), indent=2)}\n\n"
        f"Diagnosis:\n{json.dumps(diag_json, indent=2)}\n\n"
        "Search Microsoft Learn and provide grounded explanation with citations."
    )
    raw = foundry_run(
        "GroundingVerifierAgent", GROUNDING_SYSTEM_PROMPT, prompt
    )
    data = extract_json(raw)
    result = GroundedExplanation.model_validate(data)

    # Cache any fetched URLs
    for c in result.citations:
        if not cache_get(c.url):
            cache_put(c.url, c.snippet)

    return result
