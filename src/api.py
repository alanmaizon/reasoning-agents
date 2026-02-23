"""HTTP API for hosting Condor as an Azure web service."""

from __future__ import annotations

from contextlib import asynccontextmanager
from functools import lru_cache
import os
from pathlib import Path
import logging
from time import perf_counter
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple, TypeVar
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles

from .agents.coach import run_coach
from .agents.examiner import run_examiner
from .agents.grounding_verifier import run_grounding_verifier
from .agents.misconception import run_misconception
from .agents.mock_test import build_mock_test_session
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
from .observability.logging_setup import configure_logging
from .orchestration.state_store import StateStore
from .security.entra_auth import (
    EntraForbiddenError,
    EntraUnauthorizedError,
    build_entra_validator,
)


@asynccontextmanager
async def _app_lifespan(_: FastAPI):
    configure_logging()
    logging.getLogger("mdt.api").info(
        "api_startup",
        extra={"event": "api_startup"},
    )
    yield


app = FastAPI(
    title="Condor API",
    description="Condor API for AZ-900 prep",
    version="1.0.0",
    lifespan=_app_lifespan,
)

_WEB_DIR = Path(__file__).resolve().parent / "web"
app.mount("/web", StaticFiles(directory=_WEB_DIR), name="web")

T = TypeVar("T")
_bearer_scheme = HTTPBearer(auto_error=False)
_http_logger = logging.getLogger("mdt.http")


class StartSessionRequest(BaseModel):
    user_id: str = Field(default="default", min_length=1, max_length=120)
    focus_topics: List[str] = Field(default_factory=list)
    minutes: int = Field(default=30, ge=1, le=600)
    mode: Literal["adaptive", "mock_test"] = "adaptive"
    offline: bool = False


class StartSessionResponse(BaseModel):
    user_id: str
    mode: Literal["adaptive", "mock_test"] = "adaptive"
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


class FrontendConfigResponse(BaseModel):
    auth_enabled: bool
    tenant_id: Optional[str] = None
    authority: Optional[str] = None
    client_id: Optional[str] = None
    api_scope: Optional[str] = None


@lru_cache(maxsize=1)
def _state_store() -> StateStore:
    return StateStore()


@lru_cache(maxsize=1)
def _cached_foundry_runner():
    return get_foundry_runner()


@lru_cache(maxsize=1)
def _token_validator():
    return build_entra_validator()


def _authorize_v1(
    creds: Optional[HTTPAuthorizationCredentials] = Security(_bearer_scheme),
) -> Optional[Dict[str, Any]]:
    try:
        validator = _token_validator()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Authentication is misconfigured: {exc}",
        ) from exc

    if validator is None:
        return None
    if creds is None or not creds.credentials:
        raise HTTPException(
            status_code=401,
            detail="Missing bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        return validator.validate_token(creds.credentials)
    except EntraForbiddenError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except EntraUnauthorizedError as exc:
        raise HTTPException(
            status_code=401,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def _frontend_api_scope() -> Optional[str]:
    explicit = os.environ.get("FRONTEND_API_SCOPE")
    if explicit:
        return explicit.strip()

    raw_audiences = (
        os.environ.get("ENTRA_AUDIENCE")
        or os.environ.get("ENTRA_AUDIENCES")
        or ""
    )
    audience = next((a.strip() for a in raw_audiences.split(",") if a.strip()), "")
    required_scope = next(
        (s.strip() for s in os.environ.get("ENTRA_REQUIRED_SCOPES", "").split(",") if s.strip()),
        "",
    )
    if audience.startswith("api://") and required_scope:
        return f"{audience}/{required_scope}"
    return None


def _resolve_runtime(force_offline: bool) -> Tuple[bool, Any]:
    """Return (offline_used, foundry_runner)."""
    if force_offline:
        return True, None
    runner = _cached_foundry_runner()
    if runner is None:
        return True, None
    return False, runner


def _summarize_online_error(exc: Exception) -> str:
    raw = " ".join(str(exc).split())
    lower = raw.lower()

    if "resource not found" in lower or "error code: 404" in lower:
        return (
            "Endpoint or deployment not found. "
            "Check AZURE_AI_PROJECT_ENDPOINT and AZURE_AI_MODEL_DEPLOYMENT_NAME."
        )

    if (
        "defaultazurecredential failed" in lower
        or "managedidentitycredential authentication unavailable" in lower
        or "identity not found" in lower
    ):
        return (
            "Credential setup failed for online model access. "
            "Configure VM managed identity permissions or set AZURE_OPENAI_API_KEY."
        )

    if len(raw) > 240:
        return f"{raw[:237].rstrip()}..."
    return raw


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
        short_reason = _summarize_online_error(exc)
        warnings.append(
            f"{stage_name} failed online; used offline fallback. ({short_reason})"
        )
        return run_offline(), True


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.middleware("http")
async def _request_logging(
    request: Request, call_next: Callable[..., Any]
):
    request_id = request.headers.get("x-request-id") or uuid4().hex
    started = perf_counter()
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    try:
        response = await call_next(request)
    except Exception:
        duration_ms = round((perf_counter() - started) * 1000, 2)
        _http_logger.exception(
            "request_failed",
            extra={
                "event": "request_failed",
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": 500,
                "duration_ms": duration_ms,
                "client_ip": client_ip,
                "user_agent": user_agent,
            },
        )
        raise

    duration_ms = round((perf_counter() - started) * 1000, 2)
    response.headers["x-request-id"] = request_id
    _http_logger.info(
        "request_completed",
        extra={
            "event": "request_completed",
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
            "client_ip": client_ip,
            "user_agent": user_agent,
        },
    )
    return response


@app.get("/", include_in_schema=False)
def web_index() -> FileResponse:
    return FileResponse(_WEB_DIR / "index.html")


@app.get("/frontend-config", response_model=FrontendConfigResponse)
def frontend_config() -> FrontendConfigResponse:
    auth_enabled = str(os.environ.get("ENTRA_AUTH_ENABLED", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    tenant_id = os.environ.get("ENTRA_TENANT_ID")
    authority = os.environ.get("FRONTEND_AUTHORITY")
    if not authority and tenant_id:
        authority = f"https://login.microsoftonline.com/{tenant_id}"

    return FrontendConfigResponse(
        auth_enabled=auth_enabled,
        tenant_id=tenant_id,
        authority=authority,
        client_id=os.environ.get("FRONTEND_CLIENT_ID"),
        api_scope=_frontend_api_scope(),
    )


@app.get("/v1/state/{user_id}", response_model=StudentState)
def get_state(
    user_id: str,
    _claims: Optional[Dict[str, Any]] = Depends(_authorize_v1),
) -> StudentState:
    return _state_store().load(user_id)


@app.post("/v1/session/start", response_model=StartSessionResponse)
def start_session(
    req: StartSessionRequest,
    _claims: Optional[Dict[str, Any]] = Depends(_authorize_v1),
) -> StartSessionResponse:
    state = _state_store().load(req.user_id)
    state.preferred_minutes = req.minutes

    warnings: List[str] = []
    if req.mode == "mock_test":
        plan, exam = build_mock_test_session()
        offline_used = req.offline
        if req.focus_topics:
            warnings.append("Focus topics are ignored in mock_test mode.")
    else:
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
        mode=req.mode,
        offline_used=offline_used,
        warnings=warnings,
        plan=plan,
        exam=exam,
        state=state,
    )


@app.post("/v1/session/submit", response_model=SubmitSessionResponse)
def submit_session(
    req: SubmitSessionRequest,
    _claims: Optional[Dict[str, Any]] = Depends(_authorize_v1),
) -> SubmitSessionResponse:
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
