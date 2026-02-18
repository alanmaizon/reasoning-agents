"""Persistent student state across sessions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class MisconceptionRecord(BaseModel):
    misconception_id: str
    count: int = 1
    last_seen: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class StudentState(BaseModel):
    domain_scores: Dict[str, float] = Field(default_factory=dict)
    misconceptions: List[MisconceptionRecord] = Field(default_factory=list)
    preferred_minutes: int = 30
    last_session: Optional[str] = None

    def update_from_diagnosis(
        self, diagnosis_data: dict, domains_covered: List[str]
    ) -> None:
        """Merge new diagnosis into persisted state."""
        now = datetime.now(timezone.utc).isoformat()
        self.last_session = now

        # Update domain scores
        results = diagnosis_data.get("results", [])
        domain_correct: Dict[str, List[bool]] = {}
        for r in results:
            d = r.get("domain", "unknown")
            domain_correct.setdefault(d, []).append(r.get("correct", False))
        for d, outcomes in domain_correct.items():
            score = sum(outcomes) / len(outcomes) if outcomes else 0.0
            prev = self.domain_scores.get(d, 0.5)
            self.domain_scores[d] = round(0.6 * prev + 0.4 * score, 3)

        # Update misconceptions
        top = diagnosis_data.get("top_misconceptions", [])
        existing = {m.misconception_id: m for m in self.misconceptions}
        for mid in top:
            if mid in existing:
                existing[mid].count += 1
                existing[mid].last_seen = now
            else:
                existing[mid] = MisconceptionRecord(
                    misconception_id=mid, last_seen=now
                )
        self.misconceptions = list(existing.values())
