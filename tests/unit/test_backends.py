"""Tests for the pluggable mentor-backend abstraction (v0.3)."""

from __future__ import annotations

import pytest

from ammp_mcp.backends import (
    AnthropicBackend,
    LLMAnswer,
    OpenClawBackend,
    StubBackend,
    build_backend,
    parse_envelope,
)
from ammp_mcp.models import (
    AnthropicBackendConfig,
    OpenClawBackendConfig,
    StubBackendConfig,
)
from ammp_mcp.settings import Settings

pytestmark = pytest.mark.unit


# ─── Stub backend ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stub_backend_returns_low_confidence() -> None:
    b = StubBackend()
    a = await b.ask(mentor_name="Pepe", persona="x", question="anything", playbook_bodies=[])
    assert isinstance(a, LLMAnswer)
    assert 0.0 <= a.confidence < 0.5
    assert a.answer
    assert b.mode_label == "stub"
    assert b.is_live is False


# ─── Anthropic backend ────────────────────────────────────────────────────


def test_anthropic_backend_no_key_marks_not_live() -> None:
    b = AnthropicBackend(api_key=None, model="x", max_concurrent=1, timeout_seconds=1.0)
    assert b.is_live is False
    assert b.mode_label == "anthropic-direct"


@pytest.mark.asyncio
async def test_anthropic_backend_falls_back_without_key() -> None:
    b = AnthropicBackend(api_key=None, model="x", max_concurrent=1, timeout_seconds=1.0)
    a = await b.ask(mentor_name="Pepe", persona="x", question="hi", playbook_bodies=[])
    # Returns the same low-confidence shape as the stub for safety.
    assert a.confidence < 0.5
    assert a.answer


# ─── parse_envelope ───────────────────────────────────────────────────────


def test_parse_envelope_clean_json() -> None:
    a = parse_envelope('{"answer": "ok", "confidence": 0.7}')
    assert a.answer == "ok"
    assert a.confidence == 0.7


def test_parse_envelope_strips_code_fence() -> None:
    a = parse_envelope('```json\n{"answer":"a","confidence":0.5}\n```')
    assert a.answer == "a"
    assert a.confidence == 0.5


def test_parse_envelope_clamps_confidence() -> None:
    assert parse_envelope('{"answer":"x","confidence":1.7}').confidence == 1.0
    assert parse_envelope('{"answer":"x","confidence":-0.3}').confidence == 0.0


def test_parse_envelope_handles_garbage() -> None:
    a = parse_envelope("not JSON at all")
    assert a.answer  # truncated raw fallback
    assert a.confidence == 0.3


# ─── OpenClaw backend ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_openclaw_backend_posts_expected_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenClaw backend POSTs the wire-contract payload and parses the response."""
    captured: dict = {}

    class FakeResponse:
        status_code = 200
        text = '{"answer": "from-openclaw", "confidence": 0.85}'

    class FakeAsyncClient:
        def __init__(self, *_, **__) -> None:
            pass

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *_) -> None:
            pass

        async def post(self, url: str, json: dict, headers: dict) -> FakeResponse:
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr("ammp_mcp.backends.openclaw.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setenv("OC_TEST_TOKEN", "secret-token-value")

    b = OpenClawBackend(
        url="https://example.invalid/ammp/ask",
        auth_bearer_env="OC_TEST_TOKEN",
        timeout_seconds=5.0,
    )
    a = await b.ask(
        mentor_name="Pepe Arturo",
        persona="calm operator",
        question="how do I retry an OAuth callback?",
        playbook_bodies=[("OAuth playbook", "treat callback as unreliable handoff")],
    )

    assert a.answer == "from-openclaw"
    assert a.confidence == 0.85
    assert captured["url"] == "https://example.invalid/ammp/ask"
    assert captured["headers"]["Authorization"] == "Bearer secret-token-value"
    assert captured["headers"]["Content-Type"] == "application/json"
    assert captured["headers"]["User-Agent"].startswith("ammp-mcp/")
    payload = captured["json"]
    assert payload["name"] == "Pepe Arturo"
    assert payload["question"].startswith("how do I retry")
    assert payload["playbooks"] == [{"title": "OAuth playbook", "body": "treat callback as unreliable handoff"}]
    assert "ammp_version" in payload
    assert "ammp_draft" in payload


@pytest.mark.asyncio
async def test_openclaw_backend_raises_on_non_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        status_code = 503
        text = "upstream not ready"

    class FakeAsyncClient:
        def __init__(self, *_, **__) -> None:
            pass

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *_) -> None:
            pass

        async def post(self, *_, **__) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr("ammp_mcp.backends.openclaw.httpx.AsyncClient", FakeAsyncClient)
    b = OpenClawBackend(url="https://example.invalid/ammp/ask")
    with pytest.raises(RuntimeError, match="openclaw_backend_http_503"):
        await b.ask(mentor_name="P", persona="x", question="q", playbook_bodies=[])


@pytest.mark.asyncio
async def test_openclaw_backend_omits_auth_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    class FakeResponse:
        status_code = 200
        text = '{"answer": "x", "confidence": 0.5}'

    class FakeAsyncClient:
        def __init__(self, *_, **__) -> None:
            pass

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *_) -> None:
            pass

        async def post(self, url: str, json: dict, headers: dict) -> FakeResponse:
            captured["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr("ammp_mcp.backends.openclaw.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.delenv("MISSING_TOKEN_VAR", raising=False)
    b = OpenClawBackend(url="https://x", auth_bearer_env="MISSING_TOKEN_VAR")
    await b.ask(mentor_name="P", persona="x", question="q", playbook_bodies=[])
    assert "Authorization" not in captured["headers"]


# ─── Factory ──────────────────────────────────────────────────────────────


def test_factory_falls_back_to_anthropic_when_config_none(tmp_path) -> None:
    s = Settings(
        mentors_root=tmp_path / "mentors",
        mentees_file=tmp_path / "mentees.json",
        audit_log_path=tmp_path / "audit.log",
        anthropic_api_key=None,
    )
    b = build_backend(None, s)
    assert isinstance(b, AnthropicBackend)
    assert b.mode_label == "anthropic-direct"


def test_factory_builds_anthropic_with_overrides(tmp_path) -> None:
    s = Settings(
        mentors_root=tmp_path / "mentors",
        mentees_file=tmp_path / "mentees.json",
        audit_log_path=tmp_path / "audit.log",
    )
    cfg = AnthropicBackendConfig(model="custom-model", max_concurrent=5, timeout_seconds=10.0)
    b = build_backend(cfg, s)
    assert isinstance(b, AnthropicBackend)
    assert b._model == "custom-model"


def test_factory_builds_openclaw(tmp_path) -> None:
    s = Settings(
        mentors_root=tmp_path / "mentors",
        mentees_file=tmp_path / "mentees.json",
        audit_log_path=tmp_path / "audit.log",
    )
    cfg = OpenClawBackendConfig(url="https://example.invalid/ammp/ask")
    b = build_backend(cfg, s)
    assert isinstance(b, OpenClawBackend)
    assert b.mode_label == "openclaw-routed"


def test_factory_builds_stub(tmp_path) -> None:
    s = Settings(
        mentors_root=tmp_path / "mentors",
        mentees_file=tmp_path / "mentees.json",
        audit_log_path=tmp_path / "audit.log",
    )
    b = build_backend(StubBackendConfig(), s)
    assert isinstance(b, StubBackend)


# ─── Mentor model accepts backend config ──────────────────────────────────


def test_mentor_model_accepts_backend_block(tmp_path) -> None:
    from ammp_mcp.models import Mentor

    m = Mentor(
        slug="pepe",
        name="Pepe Arturo",
        persona="calm",
        playbook_dir=tmp_path,
        backend={
            "kind": "openclaw",
            "url": "https://x.invalid/ammp/ask",
            "auth_bearer_env": "FOO",
        },
    )
    assert isinstance(m.backend, OpenClawBackendConfig)
    assert m.backend.url == "https://x.invalid/ammp/ask"


def test_mentor_model_rejects_unknown_backend_kind(tmp_path) -> None:
    from pydantic import ValidationError

    from ammp_mcp.models import Mentor

    with pytest.raises(ValidationError):
        Mentor(
            slug="pepe",
            name="Pepe",
            persona="calm",
            playbook_dir=tmp_path,
            backend={"kind": "wat", "url": "x"},
        )
