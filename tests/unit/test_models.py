from __future__ import annotations

import pytest
from pydantic import ValidationError

from ammp_mcp.mentee import Mentee
from ammp_mcp.mentor import Mentor
from ammp_mcp.models import AskMentorResponse, WorkInstructionSummary

pytestmark = pytest.mark.unit


def test_mentor_slug_must_be_kebab_case(tmp_path) -> None:
    with pytest.raises(ValidationError):
        Mentor(slug="Bad Slug", name="x", persona="x", playbook_dir=tmp_path, mentor_dir=tmp_path)


def test_mentor_threshold_clamped(tmp_path) -> None:
    with pytest.raises(ValidationError):
        Mentor(
            slug="ok",
            name="x",
            persona="x",
            playbook_dir=tmp_path,
            mentor_dir=tmp_path,
            confidence_threshold=1.5,
        )


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
        relevant_instructions=[WorkInstructionSummary(id="i", title="t")],
    )
    assert r.escalation_recommended is False
    assert r.suggested_message_to_your_operator is None


def test_ask_mentor_confidence_must_be_in_range() -> None:
    with pytest.raises(ValidationError):
        AskMentorResponse(mentor="p", question="q", answer="a", confidence=1.2)


def test_prompt_helper_includes_escalation_step_when_human_mentor_set(tmp_path) -> None:
    """A mentor with a configured `human_mentor` gets the EscalateToHumanMentor step."""
    from ammp_mcp.mentor import HumanMentor, Mentor
    from ammp_mcp.server import _build_mentor_playbook_prompt

    m = Mentor(
        slug="pepe",
        name="Pepe Arturo",
        persona="calm operator",
        playbook_dir=tmp_path,
        mentor_dir=tmp_path,
        human_mentor=HumanMentor(name="Helmut Hoffer von Ankershoffen", url=None, contact=None),
    )
    prompt = _build_mentor_playbook_prompt(
        public_url="https://mcp.helmguild.com/ammp",
        mentor=m,
        playbook_id="personal-assistant-for-managers",
        playbook_name="Personal Assistant for managers",
        playbook_description="Calm, operator-grade support.",
        instruction_count=5,
    )
    # Identity
    assert "Pepe Arturo" in prompt
    assert "Personal Assistant for managers" in prompt
    assert "https://mcp.helmguild.com/ammp" in prompt
    assert 'mentor: "pepe"' in prompt
    assert 'id: "personal-assistant-for-managers"' in prompt
    # Plural-aware count
    assert "5 work instructions" in prompt
    # All four canonical tool names appear when human_mentor is set.
    for tool in ("ListPlaybooks", "GetPlaybook", "AskMentor", "EscalateToHumanMentor"):
        assert tool in prompt, tool
    # The human mentor's display name is named in the escalation step.
    assert "Helmut Hoffer von Ankershoffen" in prompt


def test_prompt_helper_omits_escalation_step_when_no_human_mentor(tmp_path) -> None:
    """A mentor without `human_mentor` doesn't get the EscalateToHumanMentor step."""
    from ammp_mcp.mentor import Mentor
    from ammp_mcp.server import _build_mentor_playbook_prompt

    m = Mentor(
        slug="strict",
        name="Strict Mentor",
        persona="high-bar",
        playbook_dir=tmp_path,
        mentor_dir=tmp_path,
    )
    prompt = _build_mentor_playbook_prompt(
        public_url="https://mcp.helmguild.com/ammp",
        mentor=m,
        playbook_id="rules",
        playbook_name="Strict rules",
        playbook_description="High-bar review rules.",
        instruction_count=1,
    )
    # Singular-aware count
    assert "1 work instruction" in prompt
    assert "1 work instructions" not in prompt
    # Other three tools still mentioned
    for tool in ("ListPlaybooks", "GetPlaybook", "AskMentor"):
        assert tool in prompt
    # No human mentor → no escalation step
    assert "EscalateToHumanMentor" not in prompt
