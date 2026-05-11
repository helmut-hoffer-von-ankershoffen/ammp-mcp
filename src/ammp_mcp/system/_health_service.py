"""Runtime probe helpers — back `ammp system health`."""

from __future__ import annotations

import json

from rich.table import Table

from ..mentor import Mentor

_ICON_OK = "[green]✓[/green]"
_ICON_FAIL = "[red]✗[/red]"
_ICON_WARN = "[yellow]![/yellow]"
_CAP_LABEL = "Capability advertisement"


def _health_probe_capability(agent_url: str, timeout: float, table: Table, problems: list[str]) -> None:
    """GET the capability advertisement and add a result row to ``table``.

    Args:
        agent_url: Full URL of the ``/.well-known/agent.json`` endpoint
            to probe.
        timeout: Per-request HTTP timeout, in seconds.
        table: The Rich table the row gets appended to (mutated).
        problems: Output list — appended to when the probe surfaces a
            problem the caller should fail-out on (mutated).
    """
    import urllib.error
    import urllib.request

    try:
        req = urllib.request.Request(agent_url, headers={"User-Agent": "ammp-health/1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read())
    except urllib.error.URLError as e:
        table.add_row(_CAP_LABEL, _ICON_FAIL, f"GET {agent_url} failed: {e}")
        problems.append(f"server unreachable at {agent_url} — boot it with `ammp serve`")
        return
    except Exception as e:
        table.add_row(_CAP_LABEL, _ICON_FAIL, f"unexpected error: {e}")
        problems.append(f"capability probe error: {e}")
        return
    ammp_block = body.get("ammp", {})
    mentor_count = len(ammp_block.get("mentors", []))
    live_count = sum(1 for m in ammp_block.get("mentors", []) if m.get("backendLive"))
    table.add_row(
        _CAP_LABEL,
        _ICON_OK,
        f"name={body.get('name')} v{body.get('version')} · {mentor_count} mentor(s), {live_count} live",
    )


def _health_probe_one_backend(slug: str, m: Mentor, timeout: float, table: Table, problems: list[str]) -> None:
    """HEAD one mentor's openclaw webhook URL and add a result row.

    No-op for mentors whose backend is not ``openclaw`` (the only
    backend kind with an HTTP target to probe). HTTP 405 / 501 are
    treated as success — the server is reachable, it just rejects HEAD.

    Args:
        slug: The mentor slug, used in row labels and problem messages.
        m: The :class:`Mentor` whose backend is being probed.
        timeout: Per-request HTTP timeout, in seconds.
        table: The Rich table the row gets appended to (mutated).
        problems: Output list — appended to when the probe surfaces a
            fatal problem (mutated).
    """
    import urllib.error
    import urllib.request

    label = f"  backend {slug} (openclaw)"
    if m.backend is None or m.backend.kind != "openclaw":
        return
    try:
        req = urllib.request.Request(m.backend.url, method="HEAD", headers={"User-Agent": "ammp-health/1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            table.add_row(label, _ICON_OK, f"HEAD {m.backend.url} → {resp.status}")
    except urllib.error.HTTPError as e:
        # 405 Method Not Allowed is fine — server is up, just rejects HEAD.
        if e.code in {405, 501}:
            table.add_row(label, _ICON_OK, f"reachable (HEAD rejected: {e.code})")
        else:
            table.add_row(label, _ICON_WARN, f"{m.backend.url} → {e}")
    except urllib.error.URLError as e:
        table.add_row(label, _ICON_FAIL, f"unreachable: {e.reason}")
        problems.append(f"openclaw backend for {slug!r} unreachable: {m.backend.url}")
    except Exception as e:
        table.add_row(label, _ICON_FAIL, f"unexpected error: {e}")
        problems.append(f"openclaw backend probe error for {slug!r}: {e}")
