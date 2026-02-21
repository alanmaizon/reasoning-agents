"""End-to-end orchestration: intake → plan → quiz → diagnose → ground → coach."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from pydantic import ValidationError

from ..models.schemas import (
    Coaching,
    Diagnosis,
    Exam,
    GroundedExplanation,
    Plan,
    StudentAnswerSheet,
)
from ..models.state import StudentState
from ..util.console import (
    console,
    print_banner,
    print_coaching,
    print_diagnosis_summary,
    print_grounded,
    print_question,
    print_step,
)
from ..util.jsonio import load_json, save_json

from ..agents.planner import run_planner
from ..agents.examiner import run_examiner
from ..agents.misconception import run_misconception
from ..agents.grounding_verifier import run_grounding_verifier
from ..agents.coach import run_coach


STATE_PATH = Path("student_state.json")


def _load_state() -> StudentState:
    raw = load_json(STATE_PATH)
    if not raw:
        return StudentState()
    try:
        return StudentState.model_validate(raw)
    except ValidationError:
        console.print(
            "[yellow]State file is invalid for current schema; starting with a fresh state.[/yellow]"
        )
        return StudentState()


def _save_state(state: StudentState) -> bool:
    try:
        save_json(STATE_PATH, state.model_dump())
        return True
    except OSError as exc:
        console.print(f"[yellow]Could not save state: {exc}[/yellow]")
        return False


def _prompt_user_intake() -> tuple[list[str], int]:
    """Prompt user for optional focus topics and daily study minutes."""
    console.print("\n[bold]Optional:[/bold] Enter focus topics (comma-separated) or press Enter to skip:")
    raw_topics = input("> ").strip()
    topics = [t.strip() for t in raw_topics.split(",") if t.strip()] if raw_topics else []

    console.print("[bold]Optional:[/bold] Daily study minutes (default 30):")
    raw_mins = input("> ").strip()
    try:
        minutes = int(raw_mins) if raw_mins else 30
    except ValueError:
        minutes = 30
    if minutes <= 0:
        minutes = 30
    return topics, minutes


def _present_quiz(exam: Exam) -> StudentAnswerSheet:
    """Present questions to the user and collect answers."""
    answers: Dict[str, int] = {}
    for i, q in enumerate(exam.questions, 1):
        print_question(i, q.stem, q.choices)
        while True:
            raw = input("Your answer (number): ").strip()
            try:
                choice = int(raw)
                if 1 <= choice <= len(q.choices):
                    answers[q.id] = choice - 1  # store 0-based
                    break
            except ValueError:
                pass
            console.print(f"  [red]Enter a number 1-{len(q.choices)}[/red]")
    return StudentAnswerSheet(answers=answers)


def run_workflow(
    offline: bool = False,
    foundry_run: Any = None,
) -> None:
    """Execute the full tutoring workflow.

    Args:
        offline: If True, use stub outputs (no API calls).
        foundry_run: Callable(agent_name, prompt) -> str for online mode.
    """
    print_banner()

    # 1. Load prior state
    state = _load_state()
    if state.last_session:
        console.print(f"[dim]Resuming — last session: {state.last_session}[/dim]")

    # 2. Intake
    print_step("1/7", "Student intake")
    focus_topics, minutes = _prompt_user_intake()
    state.preferred_minutes = minutes

    # 3. Plan
    print_step("2/7", "Planning study session")
    try:
        plan: Plan = run_planner(
            state=state,
            focus_topics=focus_topics,
            offline=offline,
            foundry_run=foundry_run,
        )
    except Exception as exc:
        if offline:
            raise
        console.print(
            f"[yellow]Planner failed online ({exc}); falling back to offline planner.[/yellow]"
        )
        plan = run_planner(
            state=state,
            focus_topics=focus_topics,
            offline=True,
            foundry_run=None,
        )
    console.print(f"  Domains: {plan.domains}  |  Questions: {plan.target_questions}")

    # 4. Quiz
    print_step("3/7", "Generating adaptive quiz")
    try:
        exam: Exam = run_examiner(
            plan=plan,
            offline=offline,
            foundry_run=foundry_run,
        )
    except Exception as exc:
        if offline:
            raise
        console.print(
            f"[yellow]Examiner failed online ({exc}); falling back to offline examiner.[/yellow]"
        )
        exam = run_examiner(
            plan=plan,
            offline=True,
            foundry_run=None,
        )
    console.print(f"  Generated {len(exam.questions)} questions")

    # 5. Present quiz & collect answers
    print_step("4/7", "Quiz time!")
    answer_sheet: StudentAnswerSheet = _present_quiz(exam)

    # 6. Diagnose
    print_step("5/7", "Diagnosing misconceptions")
    try:
        diagnosis: Diagnosis = run_misconception(
            exam=exam,
            answers=answer_sheet,
            offline=offline,
            foundry_run=foundry_run,
        )
    except Exception as exc:
        if offline:
            raise
        console.print(
            "[yellow]Misconception analysis failed online "
            f"({exc}); falling back to offline diagnosis.[/yellow]"
        )
        diagnosis = run_misconception(
            exam=exam,
            answers=answer_sheet,
            offline=True,
            foundry_run=None,
        )
    print_diagnosis_summary(diagnosis.model_dump())

    # 7. Ground explanations (only for wrong answers)
    print_step("6/7", "Grounding explanations with Microsoft Learn")
    wrong_ids = [r.id for r in diagnosis.results if not r.correct]
    wrong_questions = [q for q in exam.questions if q.id in wrong_ids]

    grounded: List[GroundedExplanation] = []
    for q in wrong_questions:
        diag_entry = next(
            (r for r in diagnosis.results if r.id == q.id), None
        )
        try:
            g = run_grounding_verifier(
                question=q,
                diagnosis_result=diag_entry,
                offline=offline,
                foundry_run=foundry_run,
            )
        except Exception as exc:
            if offline:
                raise
            console.print(
                "[yellow]Grounding failed online for "
                f"Q{q.id} ({exc}); using offline grounded explanation.[/yellow]"
            )
            g = run_grounding_verifier(
                question=q,
                diagnosis_result=diag_entry,
                offline=True,
                foundry_run=None,
            )
        grounded.append(g)
    if grounded:
        print_grounded([g.model_dump() for g in grounded])
    else:
        console.print("  [green]All correct — no grounding needed![/green]")

    # 8. Coach
    print_step("7/7", "Generating coaching & micro-drills")
    try:
        coaching: Coaching = run_coach(
            diagnosis=diagnosis,
            grounded=grounded,
            offline=offline,
            foundry_run=foundry_run,
        )
    except Exception as exc:
        if offline:
            raise
        console.print(
            f"[yellow]Coach failed online ({exc}); falling back to offline coach.[/yellow]"
        )
        coaching = run_coach(
            diagnosis=diagnosis,
            grounded=grounded,
            offline=True,
            foundry_run=None,
        )
    print_coaching(coaching.model_dump())

    # 9. Persist state
    domains_covered = list({q.domain for q in exam.questions})
    # Enrich diagnosis results with domain info for state update
    diag_dump = diagnosis.model_dump()
    for r in diag_dump["results"]:
        q_match = next((q for q in exam.questions if q.id == r["id"]), None)
        if q_match:
            r["domain"] = q_match.domain
    state.update_from_diagnosis(diag_dump, domains_covered)
    if _save_state(state):
        console.print("\n[bold green]✅ Session complete. State saved.[/bold green]\n")
    else:
        console.print("\n[bold yellow]✅ Session complete. State not saved.[/bold yellow]\n")
