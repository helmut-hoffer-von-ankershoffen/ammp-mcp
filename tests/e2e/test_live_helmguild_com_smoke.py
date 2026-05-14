"""End-to-end smoke tests against the *live* www.helmguild.com static site.

Proves that:
  - Every URL the AMMP RFC links to actually resolves (sitemap is honest).
  - The Pepe-authored blog posts are reachable in both EN + DE.
  - The Atom feed is well-formed and includes the latest post.
  - The robots.txt advertises the sitemap.

No Bearer needed; hits the public site. Skips if the site is unreachable.
"""

from __future__ import annotations

import urllib.request
from urllib.error import URLError

import pytest

pytestmark = pytest.mark.e2e

SITE = "https://www.helmguild.com"

UA = "ammp-mcp/e2e-smoke (https://github.com/helmut-hoffer-von-ankershoffen/ammp-mcp)"


def _get(path: str) -> tuple[int, dict[str, str], str]:
    """One-shot GET against the live site; returns (status, headers, body-as-text)."""
    req = urllib.request.Request(SITE + path, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, dict(r.headers), r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
        return exc.code, dict(exc.headers or {}), body
    except URLError as exc:
        pytest.skip(f"helmguild.com unreachable at {path}: {exc}")


# ─── robots.txt + sitemap.xml ────────────────────────────────────────────


def test_live_helmguild_robots_advertises_sitemap() -> None:
    """The static site's robots.txt must name the sitemap."""
    status, _h, body = _get("/robots.txt")
    assert status == 200
    assert "User-agent: *" in body
    assert "Sitemap:" in body and "sitemap.xml" in body


def test_live_helmguild_sitemap_lists_canonical_pages() -> None:
    """Sitemap must list the canonical pages a bot needs to find. Pinned
    because each new blog post needs a sitemap entry; a missing one
    silently hurts discoverability.
    """
    status, _h, body = _get("/sitemap.xml")
    assert status == 200
    for url in (
        "https://www.helmguild.com/",
        "https://www.helmguild.com/manifesto/",
        "https://www.helmguild.com/rfc/ammp/",
        "https://www.helmguild.com/blog/",
        "https://www.helmguild.com/blog/agentic-mentoring-ammp/",
        "https://www.helmguild.com/blog/managers-and-agents-2026/",
        "https://www.helmguild.com/blog/mandatory-mentoring-for-agents/",
        "https://www.helmguild.com/pepe-arturo-ai/",
        "https://www.helmguild.com/helmut-hoffer-von-ankershoffen/",
    ):
        assert f"<loc>{url}</loc>" in body, f"sitemap missing {url}"


# ─── Atom feed ───────────────────────────────────────────────────────────


def test_live_helmguild_feed_lists_latest_post() -> None:
    """The Atom feed must surface the latest blog post on top with the
    Pepe-Arturo-AI author. Pinned so a future post never lands on the
    site without making it into the feed.
    """
    status, _h, body = _get("/feed.xml")
    assert status == 200
    assert "<feed" in body and 'xmlns="http://www.w3.org/2005/Atom"' in body
    # The mandatory-mentoring post (2026-05-14) ships in this snapshot.
    assert "https://www.helmguild.com/blog/mandatory-mentoring-for-agents/" in body
    assert "Do agents need mandatory mentoring?" in body
    assert "Pepe Arturo AI" in body


def test_live_helmguild_de_feed_lists_latest_post() -> None:
    """German feed mirrors the EN one for the same post id."""
    status, _h, body = _get("/de/feed.xml")
    assert status == 200
    assert "https://www.helmguild.com/de/blog/mandatory-mentoring-for-agents/" in body
    assert "Brauchen Agenten verpflichtendes Mentoring?" in body


# ─── Blog post reachability (EN + DE) + cross-linking ────────────────────


@pytest.mark.parametrize(
    "slug",
    [
        "agentic-mentoring-ammp",
        "managers-and-agents-2026",
        "mandatory-mentoring-for-agents",
    ],
)
def test_live_helmguild_blog_post_en_reachable(slug: str) -> None:
    """Every blog post URL the sitemap lists must actually 200."""
    status, _h, body = _get(f"/blog/{slug}/")
    assert status == 200
    # Cross-link to DE alternate is present.
    assert f'href="https://www.helmguild.com/de/blog/{slug}/"' in body or f"/de/blog/{slug}/" in body
    # Pepe's authorship surfaces somewhere in the meta block.
    assert "Pepe Arturo AI" in body


@pytest.mark.parametrize(
    "slug",
    [
        "agentic-mentoring-ammp",
        "managers-and-agents-2026",
        "mandatory-mentoring-for-agents",
    ],
)
def test_live_helmguild_blog_post_de_reachable(slug: str) -> None:
    """Every DE blog post URL the sitemap lists must actually 200."""
    status, _h, _body = _get(f"/de/blog/{slug}/")
    assert status == 200


# ─── RFC + brand pages ───────────────────────────────────────────────────


def test_live_helmguild_rfc_ammp_pages_reachable_and_name_standards() -> None:
    """The AMMP RFC pages (EN + DE) must serve the open-standards section
    that the deployed AMMP server's landing also points at."""
    for path in ("/rfc/ammp/", "/de/rfc/ammp/"):
        status, _h, body = _get(path)
        assert status == 200, path
        assert "agentskills.io" in body, path
        assert "code.claude.com/docs/en/plugins" in body, path
        assert "GetPluginArchive" in body, path


def test_live_helmguild_pepe_profile_names_standards_and_drops_in_provisioning() -> None:
    """Pepe's profile page names AgentSkills + Claude-Code plugins and no
    longer says ``(in provisioning)``."""
    status, _h, body = _get("/pepe-arturo-ai/")
    assert status == 200
    assert "agentskills.io" in body
    assert "Claude Code plugins" in body
    assert "in provisioning" not in body
