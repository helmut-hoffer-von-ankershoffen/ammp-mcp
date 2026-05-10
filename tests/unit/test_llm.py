from __future__ import annotations

import pytest

from ammp_mcp.llm import LLMClient, _parse_envelope

pytestmark = pytest.mark.unit


def test_stub_mode_returns_low_confidence_when_no_api_key() -> None:
    client = LLMClient(api_key=None, model="claude-opus-4-7", max_concurrent=1, timeout_seconds=1.0)
    assert client.is_live is False


@pytest.mark.asyncio
async def test_stub_ask_returns_deterministic_low_confidence() -> None:
    client = LLMClient(api_key=None, model="claude-opus-4-7", max_concurrent=1, timeout_seconds=1.0)
    answer = await client.ask(
        mentor_name="Pepe",
        persona="calm",
        question="what",
        playbook_bodies=[],
    )
    assert 0.0 <= answer.confidence < 0.5  # stub returns 0.2 → triggers escalation
    assert answer.answer  # non-empty


def test_parse_envelope_clean_json() -> None:
    a = _parse_envelope('{"answer": "hello", "confidence": 0.8}')
    assert a.answer == "hello"
    assert a.confidence == 0.8


def test_parse_envelope_strips_code_fence() -> None:
    raw = '```json\n{"answer": "hi", "confidence": 0.5}\n```'
    a = _parse_envelope(raw)
    assert a.answer == "hi"
    assert a.confidence == 0.5


def test_parse_envelope_clamps_confidence() -> None:
    a = _parse_envelope('{"answer": "x", "confidence": 1.7}')
    assert a.confidence == 1.0
    b = _parse_envelope('{"answer": "x", "confidence": -0.3}')
    assert b.confidence == 0.0


def test_parse_envelope_handles_garbled_input() -> None:
    a = _parse_envelope("totally not JSON")
    assert a.answer  # fallback to truncated raw
    assert a.confidence == 0.3


def test_parse_envelope_recovers_from_prose_around_json() -> None:
    raw = 'Sure, here is my reply: {"answer": "ok", "confidence": 0.7} hope that helps!'
    a = _parse_envelope(raw)
    assert a.answer == "ok"
    assert a.confidence == 0.7
