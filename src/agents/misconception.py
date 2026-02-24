"""MisconceptionAgent — classifies errors into a defined taxonomy."""

from __future__ import annotations

import json
import re
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


_CHOICE_PREFIX = re.compile(r"^[A-F]\)\s*")
_LOW_SIGNAL_WHY_MARKERS = (
    "indicates confusion",
    "shows confusion",
    "selected choice",
    "correct is choice",
    "correct answer is choice",
)


def _default_misconception_for_domain(domain: str) -> str:
    return DOMAIN_TO_MISCONCEPTION.get(domain, "TERMS")


def _compact_text(value: str) -> str:
    return " ".join(value.split()).strip()


def _choice_label(index: int) -> str:
    return f"{chr(ord('A') + index)})"


def _choice_text(question: Question, index: int) -> str:
    if index < 0 or index >= len(question.choices):
        return "Unknown"
    text = _compact_text(str(question.choices[index]))
    return _CHOICE_PREFIX.sub("", text).strip() or text


def _choice_ref(question: Question, index: int) -> str:
    return f"{_choice_label(index)} {_choice_text(question, index)}"


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
    rationale = _compact_text(question.rationale_draft) or "Review this AZ-900 concept."
    correct_choice = _choice_ref(question, question.answer_key)

    if correct:
        return f"Correct. {rationale}"
    if student_answer is None:
        return f"No answer provided. Correct answer: {correct_choice}. Why: {rationale}"

    selected_choice = _choice_ref(question, student_answer)
    return f"Your answer: {selected_choice}. Correct answer: {correct_choice}. Why: {rationale}"


def _is_low_signal_why(why: str) -> bool:
    clean = _compact_text(why)
    if len(clean) < 24:
        return True
    lowered = clean.lower()
    return any(marker in lowered for marker in _LOW_SIGNAL_WHY_MARKERS)


def _merge_why(default_why: str, model_why: Any, trust_model_reasoning: bool) -> str:
    if not trust_model_reasoning or not isinstance(model_why, str):
        return default_why
    clean_model_why = _compact_text(model_why)
    if not clean_model_why or _is_low_signal_why(clean_model_why):
        return default_why
    if clean_model_why.lower() in default_why.lower():
        return default_why
    return f"{default_why} {clean_model_why}"


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
        default_why = _default_why(question, student_answer, correct)
        why = _merge_why(
            default_why=default_why,
            model_why=model_result.get("why"),
            trust_model_reasoning=trust_model_reasoning,
        )
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
                why=why,
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
                why=_default_why(q, student_ans, correct),
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
