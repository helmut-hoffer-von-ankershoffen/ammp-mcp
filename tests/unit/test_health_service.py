"""Unit tests for `system._health_service` — the runtime probes behind `ammp system health`."""

from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import patch

import pytest
from rich.table import Table

from ammp_mcp.mentor import Mentor, OpenClawBackendConfig, StubBackendConfig
from ammp_mcp.system._health_service import (
    _CAP_LABEL,
    _health_probe_capability,
    _health_probe_one_backend,
)

pytestmark = pytest.mark.unit


class _FakeResp:
    """Stand-in for the `urllib.request.urlopen()` context manager."""

    def __init__(self, payload: bytes, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def _mk_table() -> Table:
    return Table()


# ─── _health_probe_capability ─────────────────────────────────────────────


def test_capability_happy_path() -> None:
    body = {
        "name": "ammp-mcp",
        "version": "0.3.0",
        "ammp": {"mentors": [{"backendLive": True}, {"backendLive": False}]},
    }
    table = _mk_table()
    problems: list[str] = []

    with patch(
        "urllib.request.urlopen",
        return_value=_FakeResp(json.dumps(body).encode("utf-8")),
    ):
        _health_probe_capability("http://localhost:1/.well-known/agent.json", 1.0, table, problems)

    assert problems == []
    # Row count = 1 (capability row added)
    assert table.row_count == 1


def test_capability_handles_url_error() -> None:
    table = _mk_table()
    problems: list[str] = []

    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        _health_probe_capability("http://localhost:1/.well-known/agent.json", 1.0, table, problems)

    assert len(problems) == 1
    assert "server unreachable" in problems[0]


def test_capability_handles_unexpected_exception() -> None:
    table = _mk_table()
    problems: list[str] = []

    with patch(
        "urllib.request.urlopen",
        return_value=_FakeResp(b"not-json"),
    ):
        _health_probe_capability("http://localhost:1/.well-known/agent.json", 1.0, table, problems)

    assert len(problems) == 1
    assert "capability probe error" in problems[0]


def test_capability_missing_ammp_block_still_succeeds() -> None:
    body = {"name": "x", "version": "y"}  # no ammp key
    table = _mk_table()
    problems: list[str] = []

    with patch(
        "urllib.request.urlopen",
        return_value=_FakeResp(json.dumps(body).encode("utf-8")),
    ):
        _health_probe_capability("http://x/.well-known/agent.json", 1.0, table, problems)

    assert problems == []
    assert table.row_count == 1


# ─── _health_probe_one_backend ────────────────────────────────────────────


def _mentor(backend: object | None, slug: str = "pepe", tmp_path=None) -> Mentor:
    return Mentor(
        slug=slug,
        name="Pepe",
        persona="x",
        playbook_dir=tmp_path or "/tmp",
        backend=backend,
    )


def test_one_backend_no_backend_is_noop(tmp_path) -> None:
    m = _mentor(backend=None, tmp_path=tmp_path)
    table = _mk_table()
    problems: list[str] = []
    _health_probe_one_backend("pepe", m, 1.0, table, problems)
    assert table.row_count == 0
    assert problems == []


def test_one_backend_non_openclaw_is_noop(tmp_path) -> None:
    m = _mentor(backend=StubBackendConfig(), tmp_path=tmp_path)
    table = _mk_table()
    problems: list[str] = []
    _health_probe_one_backend("pepe", m, 1.0, table, problems)
    assert table.row_count == 0
    assert problems == []


def test_one_backend_head_succeeds(tmp_path) -> None:
    m = _mentor(backend=OpenClawBackendConfig(url="http://up.example/ammp/ask"), tmp_path=tmp_path)
    table = _mk_table()
    problems: list[str] = []
    with patch("urllib.request.urlopen", return_value=_FakeResp(b"", status=200)):
        _health_probe_one_backend("pepe", m, 1.0, table, problems)
    assert problems == []
    assert table.row_count == 1


@pytest.mark.parametrize("code", [405, 501])
def test_one_backend_head_rejected_is_still_reachable(code: int, tmp_path) -> None:
    m = _mentor(backend=OpenClawBackendConfig(url="http://method.example/ammp/ask"), tmp_path=tmp_path)
    table = _mk_table()
    problems: list[str] = []
    err = urllib.error.HTTPError(url=m.backend.url, code=code, msg="x", hdrs=None, fp=io.BytesIO(b""))
    with patch("urllib.request.urlopen", side_effect=err):
        _health_probe_one_backend("pepe", m, 1.0, table, problems)
    # 405/501 = server is up but rejects HEAD; treated as OK, no problem appended
    assert problems == []
    assert table.row_count == 1


def test_one_backend_other_http_error_is_warning(tmp_path) -> None:
    m = _mentor(backend=OpenClawBackendConfig(url="http://error.example/ammp/ask"), tmp_path=tmp_path)
    table = _mk_table()
    problems: list[str] = []
    err = urllib.error.HTTPError(url=m.backend.url, code=500, msg="boom", hdrs=None, fp=io.BytesIO(b""))
    with patch("urllib.request.urlopen", side_effect=err):
        _health_probe_one_backend("pepe", m, 1.0, table, problems)
    # Non-405/501 HTTP errors are warnings (server *responded* but unhappy) — no problem appended
    assert problems == []
    assert table.row_count == 1


def test_one_backend_url_error_marks_problem(tmp_path) -> None:
    m = _mentor(backend=OpenClawBackendConfig(url="http://down.example/ammp/ask"), tmp_path=tmp_path)
    table = _mk_table()
    problems: list[str] = []
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no route")):
        _health_probe_one_backend("pepe", m, 1.0, table, problems)
    assert len(problems) == 1
    assert "unreachable" in problems[0]
    assert "pepe" in problems[0]


def test_one_backend_unexpected_exception_marks_problem(tmp_path) -> None:
    m = _mentor(backend=OpenClawBackendConfig(url="http://oops.example/ammp/ask"), tmp_path=tmp_path)
    table = _mk_table()
    problems: list[str] = []
    with patch("urllib.request.urlopen", side_effect=RuntimeError("weird ssl")):
        _health_probe_one_backend("pepe", m, 1.0, table, problems)
    assert len(problems) == 1
    assert "probe error" in problems[0]


def test_module_constants_are_distinct() -> None:
    # Sanity: failure / ok / warn icons differ
    from ammp_mcp.system._health_service import _ICON_FAIL, _ICON_OK, _ICON_WARN

    assert len({_ICON_FAIL, _ICON_OK, _ICON_WARN}) == 3
    assert _CAP_LABEL == "Capability advertisement"
