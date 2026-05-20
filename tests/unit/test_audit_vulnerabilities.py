"""Tests for the `tools/audit_vulnerabilities.py` policy script.

`evaluate()` is the load-bearing bit: it splits a pip-audit JSON report
into the deps that should still fail CI and the disputed-with-rationale
entries we explicitly accept. The script lives outside the package, so
we load it through importlib rather than a normal import.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

pytestmark = pytest.mark.unit


def _load() -> ModuleType:
    """Load `tools/audit_vulnerabilities.py` as an in-memory module."""
    path = Path(__file__).resolve().parents[2] / "tools" / "audit_vulnerabilities.py"
    spec = importlib.util.spec_from_file_location("audit_vulnerabilities", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _report(*deps: dict[str, Any]) -> dict[str, Any]:
    """Wrap dep entries in the pip-audit JSON envelope shape."""
    return {"dependencies": list(deps)}


def test_clean_report_yields_no_failures_and_no_ignored() -> None:
    """A report with zero vulnerable deps produces empty failing + ignored lists."""
    av = _load()
    failing, ignored = av.evaluate(_report({"name": "anyio", "version": "4.0", "vulns": []}))
    assert failing == []
    assert ignored == []


def test_unignored_vuln_is_kept_as_failing() -> None:
    """A real, un-ignored vuln stays in `failing` so CI flips red."""
    av = _load()
    failing, ignored = av.evaluate(
        _report({"name": "fakepkg", "version": "1.0", "vulns": [{"id": "GHSA-not-on-ignore-list"}]})
    )
    assert ignored == []
    assert len(failing) == 1
    assert failing[0]["name"] == "fakepkg"
    assert failing[0]["vulns"][0]["id"] == "GHSA-not-on-ignore-list"


def test_ignored_vuln_is_filtered_and_recorded() -> None:
    """A vuln whose ID is in IGNORED_VULNS is excluded from `failing` and noted in `ignored`."""
    av = _load()
    ignored_id = next(iter(av.IGNORED_VULNS))
    failing, ignored = av.evaluate(
        _report({"name": "pyjwt", "version": "2.12.1", "vulns": [{"id": ignored_id}]})
    )
    assert failing == []
    assert ignored == [("pyjwt", "2.12.1", ignored_id)]


def test_mixed_dep_keeps_only_unignored_vulns() -> None:
    """A dep with one ignored + one real vuln still fails on the real one."""
    av = _load()
    ignored_id = next(iter(av.IGNORED_VULNS))
    failing, ignored = av.evaluate(
        _report(
            {
                "name": "hybridpkg",
                "version": "9.9",
                "vulns": [{"id": ignored_id}, {"id": "GHSA-real-vuln"}],
            }
        )
    )
    assert ignored == [("hybridpkg", "9.9", ignored_id)]
    assert len(failing) == 1
    assert [v["id"] for v in failing[0]["vulns"]] == ["GHSA-real-vuln"]


def test_every_ignored_vuln_carries_a_justification() -> None:
    """Each entry in IGNORED_VULNS must include a non-trivial rationale string."""
    av = _load()
    assert av.IGNORED_VULNS, "IGNORED_VULNS should not be empty — at least the documented disputed entry must remain."
    for vuln_id, reason in av.IGNORED_VULNS.items():
        assert reason.strip(), f"{vuln_id} has an empty justification"
        assert len(reason) > 40, f"{vuln_id} justification looks too thin — explain why we accept the risk"
