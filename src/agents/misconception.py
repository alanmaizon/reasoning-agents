"""MisconceptionAgent — classifies errors into a defined taxonomy."""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

from ..models.schemas import (
    Diagnosis,
    DiagnosisResult,
    Exam,
    StudentAnswerSheet,
    MISCONCEPTION_IDS,
)
from ..util.jsonio import extract_json


MISCONCEPTION_SYSTEM_PROMPT = """\
You are the MisconceptionAgent for an AZ-900 tutor.
Compare the student's answers against the answer key and diagnose misconceptions.
Use ONLY these misconception IDs: {ids}
Output ONLY valid JSON (no markdown):
{{
  "results": [
    {{
      "id": "<question_id>",
      "correct": true|false,
      "misconception_id": "<ID or null if correct>",
      "why": "<brief explanation>",
      "confidence": <0.0-1.0>
    }}
  ],
  "top_misconceptions": ["<ID1>", ...]
}}
""".format(ids=", ".join(MISCONCEPTION_IDS))


def _diagnose_offline(exam: Exam, answers: StudentAnswerSheet) -> Diagnosis:
    """Deterministic offline diagnosis — compare answers to answer_key."""
    results = []
    misconception_counts: dict[str, int] = {}

    # Simple domain→misconception mapping for offline mode
    domain_to_misconception = {
        "Cloud Concepts": "SRM",
        "Azure Architecture": "REGION",
        "Security": "IDAM",
        "Cost Management": "PRICING",
        "Governance": "GOV",
        "Identity": "IDAM",
        "Azure Services": "SERVICE_SCOPE",
    }

    for q in exam.questions:
        student_ans = answers.answers.get(q.id)
        correct = student_ans == q.answer_key
        mid = None
        if not correct:
            mid = domain_to_misconception.get(q.domain, "TERMS")
            misconception_counts[mid] = misconception_counts.get(mid, 0) + 1

        results.append(
            DiagnosisResult(
                id=q.id,
                correct=correct,
                misconception_id=mid,
                why=q.rationale_draft if not correct else "Correct answer.",
                confidence=0.9 if correct else 0.75,
            )
        )

    # Rank misconceptions by frequency
    top = sorted(misconception_counts, key=misconception_counts.get, reverse=True)  # type: ignore[arg-type]
    return Diagnosis(results=results, top_misconceptions=top)


def run_misconception(
    exam: Exam,
    answers: StudentAnswerSheet,
    offline: bool = False,
    foundry_run: Optional[Callable[..., str]] = None,
) -> Diagnosis:
    if offline or foundry_run is None:
        return _diagnose_offline(exam, answers)

    prompt = (
        f"Exam:\n{json.dumps(exam.model_dump(), indent=2)}\n\n"
        f"Student answers:\n{json.dumps(answers.model_dump(), indent=2)}"
    )
    raw = foundry_run("MisconceptionAgent", MISCONCEPTION_SYSTEM_PROMPT, prompt)
    data = extract_json(raw)
    return Diagnosis.model_validate(data)
