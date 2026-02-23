"""Tests for randomized 40-60 question mock mode."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents.mock_test import build_mock_test_session


def test_mock_test_session_range_and_consistency():
    plan, exam = build_mock_test_session()

    assert 40 <= plan.target_questions <= 60
    assert len(exam.questions) == plan.target_questions
    assert len(plan.domains) > 0
    assert len(plan.next_focus) > 0

    ids = [q.id for q in exam.questions]
    assert ids == [str(i) for i in range(1, len(ids) + 1)]
    assert len({q.stem for q in exam.questions}) == len(exam.questions)
    assert any("[Dropdown Menu]" in q.stem for q in exam.questions)
