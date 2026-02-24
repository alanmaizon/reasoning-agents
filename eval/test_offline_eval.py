"""Offline evaluation harness — no API calls required.

Validates:
  a) Misconception taxonomy output format
  b) Schema adherence
  c) Verifier rejects outputs without citations (simulated)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.models.schemas import (
    Coaching,
    Diagnosis,
    DiagnosisResult,
    Exam,
    GroundedExplanation,
    MicroDrill,
    Plan,
    Question,
    StudentAnswerSheet,
    MISCONCEPTION_IDS,
)
from src.models.state import StudentState
from src.orchestration.tool_policy import is_tool_allowed, ALLOWED_MCP_TOOLS
from src.util.jsonio import extract_json
from src.agents.grounding_verifier import run_grounding_verifier

# ── Helpers ─────────────────────────────────────────────────────────

CASES_PATH = Path(__file__).parent / "offline_cases.jsonl"


def _load_cases():
    cases = []
    with open(CASES_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


# ── Tests ───────────────────────────────────────────────────────────


class TestMisconceptionTaxonomy:
    """a) Misconception taxonomy output format."""

    def test_valid_taxonomy_output(self):
        data = {
            "results": [
                {
                    "id": "1",
                    "correct": False,
                    "misconception_id": "SRM",
                    "why": "Confused shared responsibility",
                    "confidence": 0.8,
                }
            ],
            "top_misconceptions": ["SRM"],
        }
        diag = Diagnosis.model_validate(data)
        assert diag.results[0].misconception_id == "SRM"
        assert "SRM" in diag.top_misconceptions

    def test_all_misconception_ids_recognized(self):
        for mid in MISCONCEPTION_IDS:
            r = DiagnosisResult(
                id="x",
                correct=False,
                misconception_id=mid,
                why="test",
                confidence=0.5,
            )
            assert r.misconception_id == mid

    def test_correct_answer_null_misconception(self):
        r = DiagnosisResult(
            id="1",
            correct=True,
            misconception_id=None,
            why="Correct",
            confidence=1.0,
        )
        assert r.misconception_id is None

    def test_wrong_answer_requires_misconception(self):
        with pytest.raises(ValidationError):
            DiagnosisResult(
                id="1",
                correct=False,
                misconception_id=None,
                why="Wrong",
                confidence=0.8,
            )

    def test_invalid_misconception_id_rejected(self):
        with pytest.raises(ValidationError):
            Diagnosis.model_validate(
                {
                    "results": [
                        {
                            "id": "1",
                            "correct": False,
                            "misconception_id": "FAKE_ID",
                            "why": "Wrong",
                            "confidence": 0.5,
                        }
                    ],
                    "top_misconceptions": ["FAKE_ID"],
                }
            )


class TestSchemaAdherence:
    """b) Schema adherence for all data models."""

    def test_plan_valid(self):
        p = Plan(
            domains=["Cloud Concepts"],
            weights={"Cloud Concepts": 1.0},
            target_questions=8,
            next_focus=["SRM"],
        )
        assert p.target_questions == 8

    def test_plan_rejects_too_many_questions(self):
        with pytest.raises(ValidationError):
            Plan(
                domains=["Security"],
                weights={"Security": 1.0},
                target_questions=61,
                next_focus=[],
            )

    def test_plan_rejects_too_few_questions(self):
        with pytest.raises(ValidationError):
            Plan(
                domains=["Security"],
                weights={"Security": 1.0},
                target_questions=7,
                next_focus=[],
            )

    def test_question_schema(self):
        q = Question(
            id="1",
            domain="Security",
            stem="Test?",
            choices=["A", "B", "C", "D"],
            answer_key=0,
            rationale_draft="Because.",
        )
        assert q.answer_key == 0

    def test_exam_max_60(self):
        questions = [
            Question(
                id=str(i), domain="d", stem="s",
                choices=["a", "b"], answer_key=0,
                rationale_draft="r",
            )
            for i in range(61)
        ]
        with pytest.raises(ValidationError):
            Exam(questions=questions)

    def test_exam_min_8(self):
        questions = [
            Question(
                id=str(i),
                domain="d",
                stem="s",
                choices=["a", "b"],
                answer_key=0,
                rationale_draft="r",
            )
            for i in range(7)
        ]
        with pytest.raises(ValidationError):
            Exam(questions=questions)

    def test_student_answer_sheet(self):
        s = StudentAnswerSheet(answers={"1": 0, "2": 3})
        assert s.answers["1"] == 0

    def test_coaching_schema(self):
        c = Coaching(
            lesson_points=["Review SRM."],
            micro_drills=[
                MicroDrill(
                    misconception_id="SRM",
                    questions=["What is SRM?"],
                )
            ],
        )
        assert len(c.micro_drills) == 1

    def test_student_state_persistence(self):
        state = StudentState()
        assert state.preferred_minutes == 30
        state.update_from_diagnosis(
            {
                "results": [
                    {"id": "1", "correct": True, "domain": "Security"},
                    {"id": "2", "correct": False, "domain": "Security"},
                ],
                "top_misconceptions": ["IDAM"],
            },
            ["Security"],
        )
        assert "IDAM" in [m.misconception_id for m in state.misconceptions]


class TestVerifierRejectsMissingCitations:
    """c) Verifier rejects outputs without citations (simulated)."""

    def test_grounded_explanation_requires_citation(self):
        with pytest.raises(ValidationError):
            GroundedExplanation(
                question_id="1",
                explanation="Some explanation",
                citations=[],
            )

    def test_grounded_explanation_with_citation(self):
        ge = GroundedExplanation(
            question_id="1",
            explanation="Correct explanation",
            citations=[
                {
                    "title": "Azure Docs",
                    "url": "https://learn.microsoft.com/test",
                    "snippet": "Short snippet here.",
                }
            ],
        )
        assert len(ge.citations) == 1

    def test_insufficient_evidence_fallback(self):
        """Verifier should provide fallback text when no real citations exist."""
        ge = GroundedExplanation(
            question_id="1",
            explanation="Insufficient evidence — please narrow your query.",
            citations=[
                {
                    "title": "Placeholder",
                    "url": "https://learn.microsoft.com",
                    "snippet": "No matching docs found.",
                }
            ],
        )
        assert "Insufficient evidence" in ge.explanation

    def test_citation_url_must_be_learn_domain(self):
        with pytest.raises(ValidationError):
            GroundedExplanation(
                question_id="1",
                explanation="Invalid citation url",
                citations=[
                    {
                        "title": "Bad domain",
                        "url": "https://example.com/docs",
                        "snippet": "This should fail domain validation.",
                    }
                ],
            )

    def test_citation_snippet_rejects_more_than_20_words(self):
        with pytest.raises(ValidationError):
            GroundedExplanation(
                question_id="1",
                explanation="Invalid citation snippet",
                citations=[
                    {
                        "title": "Azure Docs",
                        "url": "https://learn.microsoft.com/test",
                        "snippet": (
                            "one two three four five six seven eight nine ten "
                            "eleven twelve thirteen fourteen fifteen sixteen seventeen "
                            "eighteen nineteen twenty twentyone"
                        ),
                    }
                ],
            )


class TestToolPolicy:
    """Tool allow-listing policy."""

    def test_allowed_tools(self):
        assert is_tool_allowed("microsoft_docs_search")
        assert is_tool_allowed("microsoft_docs_fetch")

    def test_denied_tools(self):
        assert not is_tool_allowed("execute_code")
        assert not is_tool_allowed("delete_resource")
        assert not is_tool_allowed("")


class TestJsonExtraction:
    """Defensive JSON parsing."""

    def test_clean_json(self):
        data = extract_json('{"key": "value"}')
        assert data["key"] == "value"

    def test_json_in_markdown(self):
        raw = '```json\n{"key": "value"}\n```'
        data = extract_json(raw)
        assert data["key"] == "value"

    def test_json_with_leading_text(self):
        raw = 'Here is the output:\n{"key": "value"}'
        data = extract_json(raw)
        assert data["key"] == "value"

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError):
            extract_json("this is not json at all")


class TestOfflineCases:
    """Run test cases from offline_cases.jsonl."""

    @pytest.fixture
    def cases(self):
        return _load_cases()

    def test_taxonomy_format(self, cases):
        case = next(c for c in cases if c["case"] == "taxonomy_format")
        diag = Diagnosis.model_validate(case["input"])
        assert case["expected_valid"] is True
        assert len(diag.results) > 0

    def test_plan_schema(self, cases):
        case = next(c for c in cases if c["case"] == "schema_adherence_plan")
        plan = Plan.model_validate(case["input"])
        assert case["expected_valid"] is True
        assert plan.target_questions <= 60

    def test_verifier_rejects_empty_citations(self, cases):
        case = next(c for c in cases if c["case"] == "verifier_rejects_no_citations")
        assert case["expected_valid"] is False
        with pytest.raises(ValidationError):
            GroundedExplanation.model_validate(case["input"])

    def test_plan_too_many_questions(self, cases):
        case = next(c for c in cases if c["case"] == "plan_too_many_questions")
        assert case["expected_valid"] is False
        with pytest.raises(ValidationError):
            Plan.model_validate(case["input"])

    def test_bad_misconception_id(self, cases):
        case = next(c for c in cases if c["case"] == "bad_misconception_id")
        assert case["expected_valid"] is False
        with pytest.raises(ValidationError):
            Diagnosis.model_validate(case["input"])


class _FakeFoundryRunner:
    def __init__(self, search_payload=None, fetch_payload=None, model_output=None, fail_model=False):
        self.search_payload = search_payload or {}
        self.fetch_payload = fetch_payload or {}
        self.model_output = model_output or "{}"
        self.fail_model = fail_model
        self.tool_calls = []

    def run_mcp_tool(self, tool_name, arguments):
        self.tool_calls.append((tool_name, arguments))
        if tool_name == "microsoft_docs_search":
            return self.search_payload
        if tool_name == "microsoft_code_sample_search":
            return {}
        if tool_name == "microsoft_docs_fetch":
            return self.fetch_payload
        raise RuntimeError(f"unexpected tool: {tool_name}")

    def __call__(self, agent_name, system_prompt, user_prompt):
        if self.fail_model:
            raise RuntimeError("model failed")
        return self.model_output


class TestGroundingVerifierMcp:
    @pytest.fixture(autouse=True)
    def _cache_stub(self, monkeypatch):
        mem = {}
        monkeypatch.setattr(
            "src.agents.grounding_verifier.cache_get", lambda url: mem.get(url)
        )
        monkeypatch.setattr(
            "src.agents.grounding_verifier.cache_put",
            lambda url, content: mem.__setitem__(url, content),
        )
        return mem

    def test_grounding_uses_mcp_search_and_fetch(self):
        q = Question(
            id="1",
            domain="Security",
            stem="Which service handles identity in Azure?",
            choices=["A", "B"],
            answer_key=1,
            rationale_draft="Identity is handled by Microsoft Entra ID.",
        )
        d = DiagnosisResult(
            id="1",
            correct=False,
            misconception_id="IDAM",
            why="Confused IAM services.",
            confidence=0.8,
        )
        runner = _FakeFoundryRunner(
            search_payload={
                "results": [
                    {
                        "title": "What is Microsoft Entra ID?",
                        "url": "https://learn.microsoft.com/en-us/entra/fundamentals/whatis",
                        "snippet": "Microsoft Entra ID is cloud-based identity service.",
                    }
                ]
            },
            fetch_payload={
                "content": (
                    "Microsoft Entra ID is a cloud-based identity and access management service "
                    "that helps employees sign in and access resources."
                )
            },
            model_output=json.dumps(
                {
                    "question_id": "1",
                    "explanation": "Microsoft Entra ID provides centralized identity and access management in Azure.",
                    "citations": [
                        {
                            "title": "What is Microsoft Entra ID?",
                            "url": "https://learn.microsoft.com/en-us/entra/fundamentals/whatis",
                            "snippet": "Microsoft Entra ID is a cloud-based identity and access management service.",
                        }
                    ],
                }
            ),
        )

        ge = run_grounding_verifier(
            question=q,
            diagnosis_result=d,
            offline=False,
            foundry_run=runner,
        )

        assert ge.question_id == "1"
        assert ge.citations[0].url.startswith("https://learn.microsoft.com/")
        called_tools = [c[0] for c in runner.tool_calls]
        assert "microsoft_docs_search" in called_tools
        assert "microsoft_docs_fetch" in called_tools

    def test_grounding_falls_back_when_model_output_invalid(self):
        q = Question(
            id="9",
            domain="Cloud Concepts",
            stem="Who owns data in shared responsibility?",
            choices=["A", "B"],
            answer_key=1,
            rationale_draft="Customers always own their data.",
        )

        ge = run_grounding_verifier(
            question=q,
            diagnosis_result=None,
            offline=False,
            foundry_run=lambda *_: "not-json",
        )

        assert ge.explanation.startswith("Correct answer:")
        assert ge.citations[0].url == (
            "https://learn.microsoft.com/en-us/azure/security/fundamentals/shared-responsibility"
        )

    def test_grounding_fallback_prefers_evidence_citation(self):
        q = Question(
            id="2",
            domain="Azure Architecture",
            stem="What are Availability Zones used for?",
            choices=["A", "B"],
            answer_key=1,
            rationale_draft="Availability Zones improve high availability.",
        )
        d = DiagnosisResult(
            id="2",
            correct=False,
            misconception_id="REGION",
            why="Mixed up region and zone.",
            confidence=0.7,
        )
        runner = _FakeFoundryRunner(
            search_payload={
                "results": [
                    {
                        "title": "Azure regions and availability zones",
                        "url": "https://learn.microsoft.com/en-us/azure/reliability/availability-zones-overview",
                        "snippet": "Availability Zones are unique physical locations in an Azure region.",
                    }
                ]
            },
            fetch_payload={
                "content": "Availability Zones are physically separate datacenters within an Azure region."
            },
            fail_model=True,
        )

        ge = run_grounding_verifier(
            question=q,
            diagnosis_result=d,
            offline=False,
            foundry_run=runner,
        )

        assert ge.explanation.startswith("Correct answer:")
        assert (
            ge.citations[0].url
            == "https://learn.microsoft.com/en-us/azure/reliability/availability-zones-overview"
        )
