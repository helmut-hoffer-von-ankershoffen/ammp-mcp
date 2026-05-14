"""End-to-end install round-trip via the real ``claude`` CLI.

Drives ``scripts/e2e-claude-code-install.sh`` against the *live*
mcp.helmguild.com deployment for each of Pepe's three plugins. The
script does:

1. ``curl -H "Authorization: Bearer …"`` against ``/plugins/<name>.zip``.
2. ``unzip``.
3. ``claude plugin validate`` on the extract.
4. Wrap the extracted plugin in a throw-away local marketplace.
5. ``claude plugin marketplace add`` + ``claude plugin install``.
6. Confirm the install landed in ``.claude/settings.local.json``.
7. Trap-cleanup the marketplace + install + tmp dir.

This pins the contract the mentee's user walks after ``AskMentor``
returns a ``GetPluginArchive`` URL: download → validate → install →
plugin appears in Claude Code's installed list, including any
bundled scripts + bundled stdio MCP server.

Skipped automatically when ``claude`` is missing from PATH or when
``HELMGUILD_AMMP_BEARER`` is unset (e.g. on CI without a real
mentee token). Locally, run with::

    HELMGUILD_AMMP_BEARER=ammp-… uv run pytest tests/e2e/test_claude_code_install.py -m e2e -q
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


SCRIPT = (Path(__file__).resolve().parent.parent.parent / "scripts" / "e2e-claude-code-install.sh").resolve()


def _have_prereqs() -> tuple[bool, str]:
    """Check the script's required prerequisites are present."""
    if not SCRIPT.is_file():
        return False, f"missing helper script: {SCRIPT}"
    for tool in ("claude", "curl", "jq", "unzip"):
        if shutil.which(tool) is None:
            return False, f"missing CLI on PATH: {tool}"
    if not os.environ.get("HELMGUILD_AMMP_BEARER"):
        return False, "HELMGUILD_AMMP_BEARER is not set"
    return True, ""


@pytest.mark.parametrize(
    "plugin",
    [
        "pepe-operator-craft",
        "pepe-multi-channel-content-pipelines",
        "pepe-personal-assistant-for-managers",
    ],
)
def test_install_round_trip_via_claude_code(plugin: str) -> None:
    """Run the install dance for one plugin and assert the script's exit code."""
    ok, why = _have_prereqs()
    if not ok:
        pytest.skip(why)
    env = {**os.environ, "PLUGIN": plugin}
    proc = subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    # Surface both streams on failure so the operator can see which step broke.
    assert proc.returncode == 0, (
        f"e2e install failed for {plugin}\n"
        f"--- stdout ---\n{proc.stdout}\n"
        f"--- stderr ---\n{proc.stderr}\n"
    )
    # The script emits a final success line we can pin on.
    assert "E2E install round-trip succeeded" in proc.stdout, proc.stdout
