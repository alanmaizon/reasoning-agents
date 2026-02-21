"""HTTP API for hosting MDT as an Azure web service."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Callable, List, Tuple, TypeVar

from fastapi import FastAPI
from pydantic import BaseModel, Field

from .agents.coach import run_coach
from .agents.examiner import run_examiner
from .agents.grounding_verifier import run_grounding_verifier
from .agents.misconception import run_misconception
from .agents.planner import run_planner
from .foundry_client import get_foundry_runner
from .models.schemas import (
    Coaching,
    Diagnosis,
    Exam,
    GroundedExplanation,
    Plan,
    StudentAnswerSheet,
)
from .models.state import StudentState
from .orchestration.state_store import StateStore


app = FastAPI(
    title="MDT API",
    description="Misconception-Driven Tutor API for AZ-900 prep",
    version="1.0.0",
)

T = TypeVar("T")


class StartSessionRequest(BaseModel):
    user_id: str = Field(default="default", min_length=1, max_length=120)
    focus_topics: List[str] = Field(default_factory=list)
    minutes: int = Field(default=30, ge=1, le=600)
    offline: bool = False


class StartSessionResponse(BaseModel):
    user_id: str
    offline_used: bool
    warnings: List[str] = Field(default_factory=list)
    plan: Plan
    exam: Exam
    state: StudentState


class SubmitSessionRequest(BaseModel):
    user_id: str = Field(default="default", min_length=1, max_length=120)
    exam: Exam
    answers: StudentAnswerSheet
    offline: bool = False


class SubmitSessionResponse(BaseModel):
    user_id: str
    offline_used: bool
    warnings: List[str] = Field(default_factory=list)
    diagnosis: Diagnosis
    grounded: List[GroundedExplanation]
    coaching: Coaching
    state: StudentState


@lru_cache(maxsize=1)
def _state_store() -> StateStore:
    return StateStore()


@lru_cache(maxsize=1)
def _cached_foundry_runner():
    return get_foundry_runner()


def _resolve_runtime(force_offline: bool) -> Tuple[bool, Any]:
    """Return (offline_used, foundry_runner)."""
    if force_offline:
        return True, None
    runner = _cached_foundry_runner()
    if runner is None:
        return True, None
    return False, runner


def _run_stage(
    stage_name: str,
    allow_online: bool,
    run_online: Callable[[], T],
    run_offline: Callable[[], T],
    warnings: List[str],
) -> Tuple[T, bool]:
    if not allow_online:
        return run_offline(), True

    try:
        return run_online(), False
    except Exception as exc:
        warnings.append(
            f"{stage_name} failed online; used offline fallback. ({exc})"
        )
        return run_offline(), True


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/v1/state/{user_id}", response_model=StudentState)
def get_state(user_id: str) -> StudentState:
    return _state_store().load(user_id)


@app.post("/v1/session/start", response_model=StartSessionResponse)
def start_session(req: StartSessionRequest) -> StartSessionResponse:
    state = _state_store().load(req.user_id)
    state.preferred_minutes = req.minutes

    warnings: List[str] = []
    runtime_offline, foundry_run = _resolve_runtime(req.offline)
    allow_online = not runtime_offline
    offline_used = runtime_offline

    def run_plan_online() -> Plan:
        return run_planner(
            state=state,
            focus_topics=req.focus_topics,
            offline=False,
            foundry_run=foundry_run,
        )

    def run_plan_offline() -> Plan:
        return run_planner(
            state=state,
            focus_topics=req.focus_topics,
            offline=True,
            foundry_run=None,
        )

    plan, used_offline_for_plan = _run_stage(
        stage_name="Planner",
        allow_online=allow_online,
        run_online=run_plan_online,
        run_offline=run_plan_offline,
        warnings=warnings,
    )
    offline_used = offline_used or used_offline_for_plan

    def run_exam_online() -> Exam:
        return run_examiner(
            plan=plan,
            offline=False,
            foundry_run=foundry_run,
        )

    def run_exam_offline() -> Exam:
        return run_examiner(
            plan=plan,
            offline=True,
            foundry_run=None,
        )

    exam, used_offline_for_exam = _run_stage(
        stage_name="Examiner",
        allow_online=allow_online,
        run_online=run_exam_online,
        run_offline=run_exam_offline,
        warnings=warnings,
    )
    offline_used = offline_used or used_offline_for_exam

    try:
        _state_store().save(req.user_id, state)
    except OSError as exc:
        warnings.append(f"State save failed: {exc}")

    return StartSessionResponse(
        user_id=req.user_id,
        offline_used=offline_used,
        warnings=warnings,
        plan=plan,
        exam=exam,
        state=state,
    )


@app.post("/v1/session/submit", response_model=SubmitSessionResponse)
def submit_session(req: SubmitSessionRequest) -> SubmitSessionResponse:
    state = _state_store().load(req.user_id)
    warnings: List[str] = []

    runtime_offline, foundry_run = _resolve_runtime(req.offline)
    allow_online = not runtime_offline
    offline_used = runtime_offline

    def run_diag_online() -> Diagnosis:
        return run_misconception(
            exam=req.exam,
            answers=req.answers,
            offline=False,
            foundry_run=foundry_run,
        )

    def run_diag_offline() -> Diagnosis:
        return run_misconception(
            exam=req.exam,
            answers=req.answers,
            offline=True,
            foundry_run=None,
        )

    diagnosis, used_offline_for_diag = _run_stage(
        stage_name="Misconception",
        allow_online=allow_online,
        run_online=run_diag_online,
        run_offline=run_diag_offline,
        warnings=warnings,
    )
    offline_used = offline_used or used_offline_for_diag

    wrong_ids = [r.id for r in diagnosis.results if not r.correct]
    wrong_questions = [q for q in req.exam.questions if q.id in wrong_ids]

    grounded: List[GroundedExplanation] = []
    for question in wrong_questions:
        diag_entry = next(
            (r for r in diagnosis.results if r.id == question.id), None
        )

        def run_ground_online() -> GroundedExplanation:
            return run_grounding_verifier(
                question=question,
                diagnosis_result=diag_entry,
                offline=False,
                foundry_run=foundry_run,
            )

        def run_ground_offline() -> GroundedExplanation:
            return run_grounding_verifier(
                question=question,
                diagnosis_result=diag_entry,
                offline=True,
                foundry_run=None,
            )

        grounded_item, used_offline_for_ground = _run_stage(
            stage_name=f"Grounding Q{question.id}",
            allow_online=allow_online,
            run_online=run_ground_online,
            run_offline=run_ground_offline,
            warnings=warnings,
        )
        offline_used = offline_used or used_offline_for_ground
        grounded.append(grounded_item)

    def run_coach_online() -> Coaching:
        return run_coach(
            diagnosis=diagnosis,
            grounded=grounded,
            offline=False,
            foundry_run=foundry_run,
        )

    def run_coach_offline() -> Coaching:
        return run_coach(
            diagnosis=diagnosis,
            grounded=grounded,
            offline=True,
            foundry_run=None,
        )

    coaching, used_offline_for_coach = _run_stage(
        stage_name="Coach",
        allow_online=allow_online,
        run_online=run_coach_online,
        run_offline=run_coach_offline,
        warnings=warnings,
    )
    offline_used = offline_used or used_offline_for_coach

    domains_covered = list({q.domain for q in req.exam.questions})
    diagnosis_dump = diagnosis.model_dump()
    for result in diagnosis_dump["results"]:
        question = next((q for q in req.exam.questions if q.id == result["id"]), None)
        if question:
            result["domain"] = question.domain
    state.update_from_diagnosis(diagnosis_dump, domains_covered)

    try:
        _state_store().save(req.user_id, state)
    except OSError as exc:
        warnings.append(f"State save failed: {exc}")

    return SubmitSessionResponse(
        user_id=req.user_id,
        offline_used=offline_used,
        warnings=warnings,
        diagnosis=diagnosis,
        grounded=grounded,
        coaching=coaching,
        state=state,
    )

