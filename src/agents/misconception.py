"""MisconceptionAgent — classifies errors into a defined taxonomy."""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

from ..models.schemas import (
    Diagnosis,
    DiagnosisResult,
    Exam,
    Question,
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


DOMAIN_TO_MISCONCEPTION = {
    "Cloud Concepts": "SRM",
    "Azure Architecture": "REGION",
    "Security": "IDAM",
    "Cost Management": "PRICING",
    "Governance": "GOV",
    "Identity": "IDAM",
    "Azure Services": "SERVICE_SCOPE",
}


def _default_misconception_for_domain(domain: str) -> str:
    return DOMAIN_TO_MISCONCEPTION.get(domain, "TERMS")


def _coerce_confidence(value: Any, default: float) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return default
    if confidence < 0.0:
        return 0.0
    if confidence > 1.0:
        return 1.0
    return confidence


def _default_why(
    question: Question,
    student_answer: Optional[int],
    correct: bool,
) -> str:
    if correct:
        return "Correct answer selected."
    if student_answer is None:
        return f"No answer provided. Correct answer is choice {question.answer_key + 1}."
    return (
        f"Selected choice {student_answer + 1}; "
        f"correct is choice {question.answer_key + 1}."
    )


def _normalize_misconception_id(raw_id: Any, domain: str) -> str:
    if isinstance(raw_id, str) and raw_id in MISCONCEPTION_IDS:
        return raw_id
    return _default_misconception_for_domain(domain)


def _rank_top_misconceptions(results: list[DiagnosisResult]) -> list[str]:
    counts: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    for idx, result in enumerate(results):
        if result.correct or result.misconception_id is None:
            continue
        mid = result.misconception_id
        counts[mid] = counts.get(mid, 0) + 1
        first_seen.setdefault(mid, idx)
    return sorted(
        counts,
        key=lambda mid: (-counts[mid], first_seen[mid]),
    )


def _normalize_online_diagnosis(
    exam: Exam,
    answers: StudentAnswerSheet,
    data: Any,
) -> Diagnosis:
    raw_results = data.get("results") if isinstance(data, dict) else None
    by_question_id: dict[str, dict[str, Any]] = {}
    if isinstance(raw_results, list):
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            qid = item.get("id")
            if isinstance(qid, str):
                by_question_id[qid] = item

    normalized_results: list[DiagnosisResult] = []
    for question in exam.questions:
        model_result = by_question_id.get(question.id, {})
        student_answer = answers.answers.get(question.id)
        correct = student_answer == question.answer_key
        default_confidence = 0.9 if correct else 0.75
        model_correct = model_result.get("correct")
        trust_model_reasoning = (
            not isinstance(model_correct, bool) or model_correct == correct
        )
        why = model_result.get("why") if trust_model_reasoning else None
        normalized_results.append(
            DiagnosisResult(
                id=question.id,
                correct=correct,
                misconception_id=(
                    None
                    if correct
                    else _normalize_misconception_id(
                        model_result.get("misconception_id"),
                        question.domain,
                    )
                ),
                why=why.strip()
                if isinstance(why, str) and why.strip()
                else _default_why(question, student_answer, correct),
                confidence=_coerce_confidence(
                    model_result.get("confidence")
                    if trust_model_reasoning
                    else default_confidence,
                    default_confidence,
                ),
            )
        )

    return Diagnosis(
        results=normalized_results,
        top_misconceptions=_rank_top_misconceptions(normalized_results),
    )


def _diagnose_offline(exam: Exam, answers: StudentAnswerSheet) -> Diagnosis:
    """Deterministic offline diagnosis — compare answers to answer_key."""
    results: list[DiagnosisResult] = []

    for q in exam.questions:
        student_ans = answers.answers.get(q.id)
        correct = student_ans == q.answer_key
        mid = None
        if not correct:
            mid = _default_misconception_for_domain(q.domain)

        results.append(
            DiagnosisResult(
                id=q.id,
                correct=correct,
                misconception_id=mid,
                why=q.rationale_draft if not correct else "Correct answer.",
                confidence=0.9 if correct else 0.75,
            )
        )

    return Diagnosis(results=results, top_misconceptions=_rank_top_misconceptions(results))


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
    return _normalize_online_diagnosis(exam, answers, data)
