from __future__ import annotations

import pytest
from pydantic import ValidationError

from ammp_mcp.models import AskMentorResponse, Mentee, Mentor, PlaybookSummary

pytestmark = pytest.mark.unit


def test_mentor_slug_must_be_kebab_case(tmp_path) -> None:
    with pytest.raises(ValidationError):
        Mentor(slug="Bad Slug", name="x", persona="x", playbook_dir=tmp_path)


def test_mentor_threshold_clamped(tmp_path) -> None:
    with pytest.raises(ValidationError):
        Mentor(slug="ok", name="x", persona="x", playbook_dir=tmp_path, confidence_threshold=1.5)


def test_mentee_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        Mentee(
            slug="m",
            operator="o",
            runtime="r",
            api_key_hash="h",
            unknown_field="x",
        )


def test_ask_mentor_response_default_no_escalation() -> None:
    r = AskMentorResponse(
        mentor="pepe",
        question="q",
        answer="a",
        confidence=0.8,
        relevant_playbooks=[PlaybookSummary(id="i", title="t")],
    )
    assert r.escalation_recommended is False
    assert r.suggested_message_to_your_operator is None


def test_ask_mentor_confidence_must_be_in_range() -> None:
    with pytest.raises(ValidationError):
        AskMentorResponse(mentor="p", question="q", answer="a", confidence=1.2)
