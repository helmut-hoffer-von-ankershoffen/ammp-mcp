from __future__ import annotations

from pathlib import Path

import pytest

from ammp_mcp.audit import log_event, reset_salt_for_testing, short_hash

pytestmark = pytest.mark.unit


def test_short_hash_is_eight_chars() -> None:
    assert len(short_hash("hello")) == 8


def test_short_hash_handles_empty() -> None:
    assert short_hash(None) == "—"
    assert short_hash("") == "—"


def test_short_hash_stable_within_process() -> None:
    a = short_hash("payload")
    b = short_hash("payload")
    assert a == b


def test_short_hash_changes_after_salt_reset() -> None:
    a = short_hash("payload")
    reset_salt_for_testing()
    b = short_hash("payload")
    assert a != b, "salt rotation must produce different hashes"


def test_log_event_appends_line(tmp_path: Path) -> None:
    log_path = tmp_path / "audit.log"
    log_event(log_path, "Probe", mentor="pepe", mentee="anonymous", request_hash="abc12345")
    text = log_path.read_text(encoding="utf-8")
    assert "op=Probe" in text
    assert "mentor=pepe" in text
    assert "mentee=anonymous" in text
    assert "hash=abc12345" in text


def test_log_event_creates_parent_dir(tmp_path: Path) -> None:
    log_path = tmp_path / "nested" / "deeper" / "audit.log"
    log_event(log_path, "Probe")
    assert log_path.is_file()


def test_log_event_does_not_record_plaintext_payload(tmp_path: Path) -> None:
    """Critical privacy guarantee: the payload itself is never written."""
    log_path = tmp_path / "audit.log"
    secret = "my-secret-question-content"
    log_event(log_path, "AskMentor", request_hash=short_hash(secret))
    text = log_path.read_text(encoding="utf-8")
    assert secret not in text
