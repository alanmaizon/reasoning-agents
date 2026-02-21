"""Pydantic schemas enforcing strict JSON contracts between agents."""

from __future__ import annotations

from typing import Dict, List, Literal, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator


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
MisconceptionId = Literal[
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
        ..., ge=8, le=12, description="Number of questions (8-12)"
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

    @model_validator(mode="after")
    def _validate_answer_key(self) -> "Question":
        if self.answer_key >= len(self.choices):
            raise ValueError("answer_key must be a valid index into choices")
        return self


class Exam(BaseModel):
    questions: List[Question] = Field(..., min_length=8, max_length=12)


# ── Student answers ────────────────────────────────────────────────
class StudentAnswerSheet(BaseModel):
    answers: Dict[str, int] = Field(
        ..., description="Mapping question_id -> chosen index"
    )


# ── Diagnosis ──────────────────────────────────────────────────────
class DiagnosisResult(BaseModel):
    id: str
    correct: bool
    misconception_id: Optional[MisconceptionId] = None
    why: str
    confidence: float = Field(..., ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_correctness(self) -> "DiagnosisResult":
        if self.correct and self.misconception_id is not None:
            raise ValueError(
                "misconception_id must be null when correct is true"
            )
        if not self.correct and self.misconception_id is None:
            raise ValueError(
                "misconception_id is required when correct is false"
            )
        return self


class Diagnosis(BaseModel):
    results: List[DiagnosisResult]
    top_misconceptions: List[MisconceptionId]

    @field_validator("top_misconceptions")
    @classmethod
    def _validate_unique_top(cls, values: List[MisconceptionId]) -> List[MisconceptionId]:
        if len(values) != len(set(values)):
            raise ValueError("top_misconceptions must not contain duplicates")
        return values


# ── Grounded explanation ───────────────────────────────────────────
class Citation(BaseModel):
    title: str
    url: str
    snippet: str = Field(
        ..., description="Short snippet, <=20 words"
    )

    @field_validator("url")
    @classmethod
    def _validate_learn_url(cls, value: str) -> str:
        parsed = urlparse(value)
        host = parsed.netloc.lower()
        if parsed.scheme != "https" or not host:
            raise ValueError("Citation URL must be a valid https URL")
        if host != "learn.microsoft.com" and not host.endswith(".learn.microsoft.com"):
            raise ValueError("Citation URL must use learn.microsoft.com")
        return value

    @field_validator("snippet")
    @classmethod
    def _validate_snippet_words(cls, value: str) -> str:
        words = value.split()
        if not words:
            raise ValueError("Citation snippet must not be empty")
        if len(words) > 20:
            raise ValueError("Citation snippet must be 20 words or fewer")
        return value


class GroundedExplanation(BaseModel):
    question_id: str
    explanation: str
    citations: List[Citation] = Field(..., min_length=1)


# ── Coaching ───────────────────────────────────────────────────────
class MicroDrill(BaseModel):
    misconception_id: MisconceptionId
    questions: List[str]


class Coaching(BaseModel):
    lesson_points: List[str]
    micro_drills: List[MicroDrill]
