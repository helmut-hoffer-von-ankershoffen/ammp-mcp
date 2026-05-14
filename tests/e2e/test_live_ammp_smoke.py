"""End-to-end smoke tests against the *live* mcp.helmguild.com deployment.

These tests hit production HTTP endpoints — no in-process server, no
mocks. They prove the deployed AMMP server actually serves what the
codebase claims it serves, and that plugin payloads round-trip with
the right metadata (license, commercial flag) baked in.

Skip-policy is on prerequisites only:
  - `HELMGUILD_AMMP_BEARER` env var (a valid mentee Bearer token)
  - the AMMP server is reachable at `https://mcp.helmguild.com/ammp`

Locally, run with:

    HELMGUILD_AMMP_BEARER=ammp-… uv run pytest tests/e2e/test_live_ammp_smoke.py -m e2e -q

On CI, set the secret + `vars.AMMP_E2E_ENABLED=true` (see
`.github/workflows/e2e-install.yml`).
"""

from __future__ import annotations

import io
import json
import os
import urllib.request
import zipfile
from urllib.error import URLError

import pytest

pytestmark = pytest.mark.e2e


AMMP_URL = os.environ.get("AMMP_URL", "https://mcp.helmguild.com/ammp")
PEPE_PLUGINS = (
    "pepe-operator-craft",
    "pepe-multi-channel-content-pipelines",
    "pepe-personal-assistant-for-managers",
)
EXPECTED_OPS = {
    "ListMentors",
    "ListPlaybooks",
    "GetPlaybook",
    "GetSkill",
    "SearchPlaybooks",
    "AskMentor",
    "EscalateToHuman",
    "EscalateToHumanMentor",
    "GetEscalation",
    "GetPluginArchive",
    "GetSystemInfo",
}
EXPECTED_LICENSE = "LicenseRef-helmguild-mentoring-1.0"


def _bearer() -> str:
    """Return the Bearer token from env, skipping the test if absent."""
    token = os.environ.get("HELMGUILD_AMMP_BEARER")
    if not token:
        pytest.skip("HELMGUILD_AMMP_BEARER not set")
    return token


def _http(
    url: str, *, headers: dict[str, str] | None = None, timeout: float = 15.0
) -> tuple[int, dict[str, str], bytes]:
    """One-shot HTTP GET that returns (status, headers, body).

    Cloudflare in front of mcp.helmguild.com blocks urllib's default
    ``Python-urllib/3.x`` User-Agent with a 403; sending a real UA
    makes the request indistinguishable from a normal client.
    """
    merged = {
        "User-Agent": "ammp-mcp/e2e-smoke (https://github.com/helmut-hoffer-von-ankershoffen/ammp-mcp)",
        **(headers or {}),
    }
    req = urllib.request.Request(url, headers=merged)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as exc:
        # 4xx / 5xx are NOT skip conditions — the test caller asked for
        # that URL and needs to assert on the status. Return them so
        # the caller decides whether to skip or fail.
        body = exc.read() if hasattr(exc, "read") else b""
        return exc.code, dict(exc.headers or {}), body
    except URLError as exc:
        pytest.skip(f"live AMMP server unreachable at {url}: {exc}")


# ─── /.well-known/agent.json — capability advertisement ───────────────────


def test_live_capability_lists_expected_operations_and_mentor() -> None:
    """The deployed server's capability JSON must advertise every op the README claims.

    Pinned because the canonical reader path is: discover the server via
    its agent.json, decide which ops to call. A drift between the
    deployed ops set and the README would mislead a mentee at the very
    first step.
    """
    status, _h, body = _http(f"{AMMP_URL}/.well-known/agent.json")
    assert status == 200
    payload = json.loads(body)
    assert payload["name"] == "ammp-mcp"
    assert payload["version"].startswith("0."), f"unexpected version {payload['version']!r}"
    assert payload["ammp"]["tracks"] == ["mentoring"]
    assert payload["ammp"]["privacyPosture"]["retention"] == "no-retention"
    assert payload["ammp"]["privacyPosture"]["requireAuth"] is True
    assert set(payload["operations"]) == EXPECTED_OPS, payload["operations"]
    slugs = {m["slug"] for m in payload["ammp"]["mentors"]}
    assert "pepe" in slugs, slugs
    pepe = next(m for m in payload["ammp"]["mentors"] if m["slug"] == "pepe")
    assert pepe["humanMentor"]["name"] == "Helmut Hoffer von Ankershoffen"


# ─── Crawler-discoverability — robots.txt + sitemap.xml ───────────────────


def test_live_robots_txt_advertises_sitemap_and_disallows_mcp_transport() -> None:
    """Bots must find the sitemap; MCP transport is bot-irrelevant and gated."""
    status, headers, body = _http(f"{AMMP_URL}/robots.txt")
    assert status == 200
    assert "text/plain" in headers.get("Content-Type", "")
    text = body.decode("utf-8")
    assert "User-agent: *" in text
    assert "Allow: /" in text
    assert "Sitemap:" in text and "sitemap.xml" in text
    assert "Disallow: /mcp/" in text
    assert "Disallow: /plugins/" in text


def test_live_sitemap_xml_lists_bilingual_landing_and_agent_card() -> None:
    """Sitemap lists the EN + DE landings (with hreflang) plus the capability card."""
    status, headers, body = _http(f"{AMMP_URL}/sitemap.xml")
    assert status == 200
    assert "xml" in headers.get("Content-Type", "")
    text = body.decode("utf-8")
    for needle in (
        f"<loc>{AMMP_URL}/</loc>",
        f"<loc>{AMMP_URL}/de/</loc>",
        f"<loc>{AMMP_URL}/.well-known/agent.json</loc>",
        'hreflang="en"',
        'hreflang="de"',
        'hreflang="x-default"',
    ):
        assert needle in text, f"sitemap missing {needle!r}"


# ─── Landing page — open-standards + commercial badges ────────────────────


def test_live_landing_renders_open_standards_section_and_commercial_badges() -> None:
    """The EN landing must name the open standards + render `Commercial` badges
    on every plugin-backed playbook served by this deployment.

    Pinned because both of those are policy-visible surfaces — a regression
    that hides them silently misrepresents the deployment.
    """
    status, _h, body = _http(f"{AMMP_URL}/")
    assert status == 200
    text = body.decode("utf-8")
    # Standards section
    assert "Built on open standards" in text
    assert "agentskills.io" in text
    assert "code.claude.com/docs/en/plugins" in text
    # Helmguild-plugins is private; AMMP brokers via GetPluginArchive
    assert "GetPluginArchive" in text
    # Commercial badges — Pepe's plugins are commercial in production
    assert "pb-commercial" in text
    assert "Commercial" in text


# ─── Per-plugin zip license + commercial round-trip ──────────────────────
#
# (We don't probe `GetPluginArchive` over the MCP wire directly here:
# FastMCP's streamable-HTTP transport requires a session handshake
# which would add a 3-call dependency for no extra contract. The
# install-side scripts/e2e-claude-code-install.sh exercises that path
# via the full FastMCP Python client. What this test asserts is the
# HTTP-route side of the contract — the actual zip payload mentees
# receive.)


@pytest.mark.parametrize("plugin", PEPE_PLUGINS)
def test_live_plugin_zip_carries_license_metadata(plugin: str) -> None:
    """The zip downloaded from production must bake in the proprietary
    Helmguild Mentoring License on plugin.json + every SKILL.md
    frontmatter. Pinned so the license sweep doesn't silently revert
    in some future migration.

    The commercial flag is carried on the marketplace.json side only
    (Claude Code's plugin.json schema rejects custom keys); its
    visibility is asserted via the landing-page badge in
    ``test_live_landing_renders_open_standards_section_and_commercial_badges``.
    """
    bearer = _bearer()
    status, headers, body = _http(
        f"{AMMP_URL}/plugins/{plugin}.zip",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert status == 200, status
    assert "zip" in headers.get("Content-Type", "")
    assert body[:2] == b"PK"
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        manifest = json.loads(z.read(f"{plugin}/.claude-plugin/plugin.json"))
        assert manifest["license"] == EXPECTED_LICENSE, manifest
        # Each SKILL.md frontmatter carries the same license id.
        skill_paths = [n for n in z.namelist() if n.startswith(f"{plugin}/skills/") and n.endswith("/SKILL.md")]
        assert skill_paths, f"no SKILL.md files in {plugin}.zip"
        for sk in skill_paths:
            head = z.read(sk).decode("utf-8")[:500]
            assert f"license: {EXPECTED_LICENSE}" in head, (sk, head)


# ─── 401 unauth path — Bearer-gated zip route ────────────────────────────


def test_live_plugin_zip_route_rejects_missing_bearer() -> None:
    """The `/plugins/<name>.zip` route MUST 401 without a Bearer.

    Pinned because the commercial license depends on the route gating —
    a regression that opens the route turns the marketplace public.
    """
    plugin = PEPE_PLUGINS[0]
    status, _h, _b = _http(f"{AMMP_URL}/plugins/{plugin}.zip")
    assert status == 401, f"expected 401, got {status}"
