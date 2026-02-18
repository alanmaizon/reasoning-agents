"""PlannerAgent — chooses domains + question mix based on student state."""

from __future__ import annotations

import json
from typing import Any, Callable, List, Optional

from ..models.schemas import Plan
from ..models.state import StudentState
from ..util.jsonio import extract_json


PLANNER_SYSTEM_PROMPT = """\
You are the PlannerAgent for an AZ-900 certification tutor.
Given the student's current state and optional focus topics, produce a study plan.
Output ONLY valid JSON matching this schema (no markdown, no explanation):
{
  "domains": ["<domain1>", ...],
  "weights": {"<domain1>": 0.4, ...},
  "target_questions": <int 8-12>,
  "next_focus": ["<area1>", ...]
}
Domains must be from: Cloud Concepts, Azure Architecture, Azure Services,
Security, Identity, Governance, Cost Management, SLAs.
"""


def _build_prompt(state: StudentState, focus_topics: List[str]) -> str:
    parts = ["Student state:"]
    if state.domain_scores:
        parts.append(f"Domain scores: {json.dumps(state.domain_scores)}")
    if state.misconceptions:
        parts.append(
            "Past misconceptions: "
            + ", ".join(m.misconception_id for m in state.misconceptions)
        )
    parts.append(f"Preferred daily minutes: {state.preferred_minutes}")
    if focus_topics:
        parts.append(f"Focus topics requested: {', '.join(focus_topics)}")
    else:
        parts.append("No specific focus requested — balance across domains.")
    return "\n".join(parts)


# ── Offline stub ────────────────────────────────────────────────────
_STUB_PLAN = Plan(
    domains=["Cloud Concepts", "Azure Architecture", "Security"],
    weights={"Cloud Concepts": 0.4, "Azure Architecture": 0.35, "Security": 0.25},
    target_questions=8,
    next_focus=["Shared Responsibility Model", "Availability Zones"],
)


def run_planner(
    state: StudentState,
    focus_topics: List[str],
    offline: bool = False,
    foundry_run: Optional[Callable[..., str]] = None,
) -> Plan:
    if offline or foundry_run is None:
        return _STUB_PLAN

    prompt = _build_prompt(state, focus_topics)
    raw = foundry_run("PlannerAgent", PLANNER_SYSTEM_PROMPT, prompt)
    data = extract_json(raw)
    return Plan.model_validate(data)
