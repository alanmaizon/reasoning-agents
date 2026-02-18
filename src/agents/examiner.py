"""ExaminerAgent — generates an adaptive diagnostic quiz."""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

from ..models.schemas import Exam, Plan, Question
from ..util.jsonio import extract_json


EXAMINER_SYSTEM_PROMPT = """\
You are the ExaminerAgent for an AZ-900 certification tutor.
Given a study plan, generate a multiple-choice quiz.
Output ONLY valid JSON matching this schema (no markdown, no explanation):
{
  "questions": [
    {
      "id": "1",
      "domain": "<domain>",
      "stem": "<question text>",
      "choices": ["A) ...", "B) ...", "C) ...", "D) ..."],
      "answer_key": <0-based index>,
      "rationale_draft": "<brief rationale>"
    }
  ]
}
Generate between 8 and 12 questions. Ensure answer_key is the 0-based index of
the correct choice. Cover domains according to the plan weights.
"""


# ── Offline stub ────────────────────────────────────────────────────
_STUB_QUESTIONS = [
    Question(
        id="1",
        domain="Cloud Concepts",
        stem="Which cloud model allows organizations to share responsibility for security with the cloud provider?",
        choices=[
            "A) Private cloud only",
            "B) Shared responsibility model",
            "C) On-premises model",
            "D) Hybrid DNS model",
        ],
        answer_key=1,
        rationale_draft="The shared responsibility model divides security tasks between provider and customer.",
    ),
    Question(
        id="2",
        domain="Azure Architecture",
        stem="What is the primary purpose of Azure Availability Zones?",
        choices=[
            "A) Reduce subscription costs",
            "B) Provide high availability within a region",
            "C) Replace Azure regions entirely",
            "D) Encrypt data at rest",
        ],
        answer_key=1,
        rationale_draft="Availability Zones are physically separate locations within a region for HA.",
    ),
    Question(
        id="3",
        domain="Security",
        stem="Which Azure service provides centralized identity management?",
        choices=[
            "A) Azure Firewall",
            "B) Microsoft Entra ID",
            "C) Azure Key Vault",
            "D) Azure Monitor",
        ],
        answer_key=1,
        rationale_draft="Microsoft Entra ID (formerly Azure AD) is the identity service.",
    ),
    Question(
        id="4",
        domain="Cloud Concepts",
        stem="In the shared responsibility model, who is always responsible for the data?",
        choices=[
            "A) Microsoft",
            "B) The customer",
            "C) Both equally",
            "D) Neither — it's automated",
        ],
        answer_key=1,
        rationale_draft="The customer is always responsible for their data, regardless of cloud model.",
    ),
    Question(
        id="5",
        domain="Azure Architecture",
        stem="What is a 'region pair' in Azure?",
        choices=[
            "A) Two VMs in the same availability set",
            "B) Two geographically close regions for disaster recovery",
            "C) A primary and secondary database",
            "D) Two network interfaces on one VM",
        ],
        answer_key=1,
        rationale_draft="Region pairs are two regions within the same geography for DR.",
    ),
    Question(
        id="6",
        domain="Cloud Concepts",
        stem="Which cloud service model provides the most control over the underlying infrastructure?",
        choices=[
            "A) SaaS",
            "B) PaaS",
            "C) IaaS",
            "D) FaaS",
        ],
        answer_key=2,
        rationale_draft="IaaS gives the most control over VMs, networking, storage.",
    ),
    Question(
        id="7",
        domain="Security",
        stem="What does Azure RBAC stand for?",
        choices=[
            "A) Resource-Based Access Control",
            "B) Role-Based Access Control",
            "C) Region-Based Access Configuration",
            "D) Risk-Based Authentication Check",
        ],
        answer_key=1,
        rationale_draft="Azure RBAC = Role-Based Access Control.",
    ),
    Question(
        id="8",
        domain="Azure Architecture",
        stem="Which component is NOT part of Azure's global infrastructure?",
        choices=[
            "A) Regions",
            "B) Availability Zones",
            "C) Edge Locations",
            "D) Subscriptions",
        ],
        answer_key=3,
        rationale_draft="Subscriptions are a billing/management construct, not physical infrastructure.",
    ),
]
_STUB_EXAM = Exam(questions=_STUB_QUESTIONS)


def run_examiner(
    plan: Plan,
    offline: bool = False,
    foundry_run: Optional[Callable[..., str]] = None,
) -> Exam:
    if offline or foundry_run is None:
        return _STUB_EXAM

    prompt = f"Study plan:\n{json.dumps(plan.model_dump(), indent=2)}"
    raw = foundry_run("ExaminerAgent", EXAMINER_SYSTEM_PROMPT, prompt)
    data = extract_json(raw)
    return Exam.model_validate(data)
