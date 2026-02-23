"""Regression tests for deterministic diagnosis accuracy."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents.misconception import run_misconception
from src.models.schemas import Exam, Question, StudentAnswerSheet


def _build_exam() -> Exam:
    domains = [
        "Cloud Concepts",
        "Azure Architecture",
        "Security",
        "Cost Management",
        "Governance",
        "Identity",
        "Azure Services",
        "Cloud Concepts",
    ]
    questions = [
        Question(
            id=str(i + 1),
            domain=domain,
            stem=f"Question {i + 1}",
            choices=["A", "B", "C", "D"],
            answer_key=1,
            rationale_draft=f"Rationale {i + 1}",
        )
        for i, domain in enumerate(domains)
    ]
    return Exam(questions=questions)


def test_online_diagnosis_forces_deterministic_correctness():
    exam = _build_exam()
    answers = StudentAnswerSheet(
        answers={str(i): 1 for i in range(1, 9)} | {"2": 0}
    )

    raw = {
        "results": [
            {
                "id": "1",
                "correct": False,
                "misconception_id": "SRM",
                "why": "Model claimed incorrect.",
                "confidence": 0.2,
            },
            {
                "id": "2",
                "correct": True,
                "misconception_id": None,
                "why": "Model claimed correct.",
                "confidence": 0.95,
            },
        ],
        "top_misconceptions": ["SRM"],
    }

    diagnosis = run_misconception(
        exam=exam,
        answers=answers,
        offline=False,
        foundry_run=lambda *_: json.dumps(raw),
    )
    by_id = {r.id: r for r in diagnosis.results}

    assert by_id["1"].correct is True
    assert by_id["1"].misconception_id is None
    assert by_id["2"].correct is False
    assert by_id["2"].misconception_id == "REGION"
    assert diagnosis.top_misconceptions == ["REGION"]


def test_online_diagnosis_normalizes_invalid_misconception_ids():
    exam = _build_exam()
    answers = StudentAnswerSheet(
        answers={str(i): 1 for i in range(1, 9)} | {"3": 0, "4": 0}
    )

    raw = {
        "results": [
            {
                "id": "3",
                "correct": False,
                "misconception_id": "NOT_REAL",
                "why": "",
                "confidence": "1.7",
            }
        ],
        "top_misconceptions": ["SRM", "GOV"],
    }

    diagnosis = run_misconception(
        exam=exam,
        answers=answers,
        offline=False,
        foundry_run=lambda *_: json.dumps(raw),
    )
    by_id = {r.id: r for r in diagnosis.results}

    assert by_id["3"].misconception_id == "IDAM"
    assert by_id["3"].confidence == 1.0
    assert by_id["4"].misconception_id == "PRICING"
    assert diagnosis.top_misconceptions == ["IDAM", "PRICING"]


def test_online_diagnosis_marks_missing_answers_as_incorrect():
    exam = _build_exam()
    answers = StudentAnswerSheet(
        answers={str(i): 1 for i in range(1, 9) if i != 5}
    )

    raw = {
        "results": [
            {
                "id": "5",
                "correct": True,
                "misconception_id": None,
                "why": "Model marked this as correct.",
                "confidence": 0.99,
            }
        ]
    }

    diagnosis = run_misconception(
        exam=exam,
        answers=answers,
        offline=False,
        foundry_run=lambda *_: json.dumps(raw),
    )
    by_id = {r.id: r for r in diagnosis.results}

    assert by_id["5"].correct is False
    assert by_id["5"].misconception_id == "GOV"
    assert "No answer provided" in by_id["5"].why
