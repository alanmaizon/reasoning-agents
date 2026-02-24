"""Quality tuning regressions for diagnosis and grounding agents."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents.grounding_verifier import run_grounding_verifier
from src.agents.misconception import run_misconception
from src.models.schemas import DiagnosisResult, Exam, Question, StudentAnswerSheet


def test_misconception_uses_question_aware_why_when_model_why_is_generic():
    filler_questions = [
        Question(
            id=str(i),
            domain="Cloud Concepts",
            stem=f"Filler question {i}",
            choices=["A) One", "B) Two", "C) Three", "D) Four"],
            answer_key=0,
            rationale_draft="Filler rationale.",
        )
        for i in range(1, 8)
    ]
    target_question = Question(
        id="10",
        domain="Azure Services",
        stem="Which is an example of a Platform as a Service offering in Azure?",
        choices=[
            "A) Azure Virtual Machines",
            "B) Azure App Service",
            "C) Azure Virtual Network",
            "D) Azure SQL Data Warehouse",
        ],
        answer_key=1,
        rationale_draft=(
            "Azure App Service is a managed platform for hosting web apps and APIs."
        ),
    )
    exam = Exam(
        questions=[*filler_questions, target_question]
    )
    answers = StudentAnswerSheet(
        answers={str(i): 0 for i in range(1, 8)} | {"10": 3}
    )
    raw = {
        "results": [
            {
                "id": "10",
                "correct": False,
                "misconception_id": "SERVICE_SCOPE",
                "why": (
                    "Selecting 'Azure SQL Data Warehouse' indicates confusion; "
                    "'Azure App Service' is the correct PaaS offering."
                ),
                "confidence": 0.84,
            }
        ],
        "top_misconceptions": ["SERVICE_SCOPE"],
    }

    diagnosis = run_misconception(
        exam=exam,
        answers=answers,
        offline=False,
        foundry_run=lambda *_: json.dumps(raw),
    )

    why = {r.id: r.why for r in diagnosis.results}["10"]
    assert "Your answer: D) Azure SQL Data Warehouse." in why
    assert "Correct answer: B) Azure App Service." in why
    assert "managed platform for hosting web apps and APIs" in why
    assert "indicates confusion" not in why


class _LowSignalGroundingRunner:
    """Runner that returns low-signal placeholder grounding output."""

    def __call__(self, agent_name: str, system_prompt: str, user_prompt: str) -> str:
        return json.dumps(
            {
                "question_id": "10",
                "explanation": "Insufficient evidence — please narrow your query.",
                "citations": [
                    {
                        "title": "Microsoft Learn",
                        "url": "https://learn.microsoft.com/",
                        "snippet": "No matching Microsoft Learn evidence retrieved yet.",
                    }
                ],
            }
        )


def test_grounding_replaces_placeholder_output_with_domain_fallback():
    question = Question(
        id="10",
        domain="Azure Services",
        stem="Which is an example of a Platform as a Service offering in Azure?",
        choices=[
            "A) Azure Virtual Machines",
            "B) Azure App Service",
            "C) Azure Virtual Network",
            "D) Azure SQL Data Warehouse",
        ],
        answer_key=1,
        rationale_draft=(
            "Azure App Service is a managed platform for hosting web apps and APIs."
        ),
    )
    diagnosis = DiagnosisResult(
        id="10",
        correct=False,
        misconception_id="SERVICE_SCOPE",
        why="Selected an IaaS/data service option instead of a PaaS host.",
        confidence=0.8,
    )

    result = run_grounding_verifier(
        question=question,
        diagnosis_result=diagnosis,
        offline=False,
        foundry_run=_LowSignalGroundingRunner(),
    )

    assert result.question_id == "10"
    assert result.explanation.startswith("Correct answer: B) Azure App Service.")
    assert "Insufficient evidence" not in result.explanation
    assert result.citations
    assert "azure/app-service/overview" in result.citations[0].url
