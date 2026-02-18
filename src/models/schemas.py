"""Pydantic schemas enforcing strict JSON contracts between agents."""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# ── Misconception taxonomy ──────────────────────────────────────────
MISCONCEPTION_IDS = [
    "SRM",
    "IDAM",
    "REGION",
    "PRICING",
    "GOV",
    "SEC",
    "SERVICE_SCOPE",
    "TERMS",
]


# ── Plan ────────────────────────────────────────────────────────────
class Plan(BaseModel):
    domains: List[str] = Field(..., description="Domains to cover")
    weights: Dict[str, float] = Field(
        ..., description="Weight per domain (0-1)"
    )
    target_questions: int = Field(
        ..., ge=1, le=12, description="Number of questions (max 12)"
    )
    next_focus: List[str] = Field(
        ..., description="Priority areas for next session"
    )


# ── Question / Exam ────────────────────────────────────────────────
class Question(BaseModel):
    id: str
    domain: str
    stem: str
    choices: List[str] = Field(..., min_length=2, max_length=6)
    answer_key: int = Field(..., ge=0, description="0-based index")
    rationale_draft: str


class Exam(BaseModel):
    questions: List[Question] = Field(..., min_length=1, max_length=12)


# ── Student answers ────────────────────────────────────────────────
class StudentAnswerSheet(BaseModel):
    answers: Dict[str, int] = Field(
        ..., description="Mapping question_id -> chosen index"
    )


# ── Diagnosis ──────────────────────────────────────────────────────
class DiagnosisResult(BaseModel):
    id: str
    correct: bool
    misconception_id: Optional[str] = None
    why: str
    confidence: float = Field(..., ge=0.0, le=1.0)


class Diagnosis(BaseModel):
    results: List[DiagnosisResult]
    top_misconceptions: List[str]


# ── Grounded explanation ───────────────────────────────────────────
class Citation(BaseModel):
    title: str
    url: str
    snippet: str = Field(
        ..., max_length=200, description="<=20 words snippet"
    )


class GroundedExplanation(BaseModel):
    question_id: str
    explanation: str
    citations: List[Citation] = Field(..., min_length=1)


# ── Coaching ───────────────────────────────────────────────────────
class MicroDrill(BaseModel):
    misconception_id: str
    questions: List[str]


class Coaching(BaseModel):
    lesson_points: List[str]
    micro_drills: List[MicroDrill]
