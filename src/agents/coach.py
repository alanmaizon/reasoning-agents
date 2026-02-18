"""CoachAgent — generates concise remediation + micro-drills."""

from __future__ import annotations

import json
from typing import Any, Callable, List, Optional

from ..models.schemas import (
    Coaching,
    Diagnosis,
    GroundedExplanation,
    MicroDrill,
)
from ..util.jsonio import extract_json


COACH_SYSTEM_PROMPT = """\
You are the CoachAgent for an AZ-900 tutor.
Given a diagnosis and grounded explanations, produce lesson points and micro-drills.
Output ONLY valid JSON:
{
  "lesson_points": ["<point1>", ...],
  "micro_drills": [
    {
      "misconception_id": "<ID>",
      "questions": ["<drill question 1>", ...]
    }
  ]
}
Keep lesson points concise (1-2 sentences each). Generate 2-3 drill questions
per misconception. Focus on the top misconceptions from the diagnosis.
"""


def _offline_coach(
    diagnosis: Diagnosis, grounded: List[GroundedExplanation]
) -> Coaching:
    """Deterministic stub coaching output."""
    lesson_points = [
        "Review the shared responsibility model — customer always owns data.",
        "Availability Zones provide HA within a single region, not across regions.",
        "Microsoft Entra ID is the central identity service (formerly Azure AD).",
    ]
    drills = []
    for mid in diagnosis.top_misconceptions[:3]:
        drills.append(
            MicroDrill(
                misconception_id=mid,
                questions=[
                    f"Explain the concept related to {mid} in your own words.",
                    f"Give a real-world example where {mid} confusion could cause issues.",
                ],
            )
        )
    return Coaching(lesson_points=lesson_points, micro_drills=drills)


def run_coach(
    diagnosis: Diagnosis,
    grounded: List[GroundedExplanation],
    offline: bool = False,
    foundry_run: Optional[Callable[..., str]] = None,
) -> Coaching:
    if offline or foundry_run is None:
        return _offline_coach(diagnosis, grounded)

    prompt = (
        f"Diagnosis:\n{json.dumps(diagnosis.model_dump(), indent=2)}\n\n"
        f"Grounded explanations:\n"
        + json.dumps([g.model_dump() for g in grounded], indent=2)
    )
    raw = foundry_run("CoachAgent", COACH_SYSTEM_PROMPT, prompt)
    data = extract_json(raw)
    return Coaching.model_validate(data)
