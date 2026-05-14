"""Static install validation — backs `ammp system status`."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.table import Table

from ..mentee import load_mentees
from ..mentor import Mentor, load_mentors
from ..playbook import load_playbooks
from ..settings import Settings

console = Console()

_ICON_OK = "[green]✓[/green]"
_ICON_FAIL = "[red]✗[/red]"
_ICON_WARN = "[yellow]![/yellow]"


@dataclass
class _StatusReporter:
    """Collects status-check rows + outcome lists. One closure-free unit per run."""

    table: Table
    problems: list[str]
    notes: list[str]

    def ok(self, name: str, detail: str = "") -> None:
        """Record a green status row — no problem and no warning surfaced.

        Args:
            name: Left-column row label.
            detail: Right-column free-form detail (may be empty).
        """
        self.table.add_row(name, _ICON_OK, detail)

    def warn(self, name: str, detail: str) -> None:
        """Record a yellow status row and append a non-fatal note.

        Args:
            name: Left-column row label.
            detail: Right-column detail; also appended to ``self.notes``
                so the summary line lists it.
        """
        self.table.add_row(name, _ICON_WARN, detail)
        self.notes.append(f"{name}: {detail}")

    def fail(self, name: str, detail: str) -> None:
        """Record a red status row and append a fatal problem.

        Args:
            name: Left-column row label.
            detail: Right-column detail; also appended to ``self.problems``
                so :func:`_status_emit_summary` exits non-zero.
        """
        self.table.add_row(name, _ICON_FAIL, detail)
        self.problems.append(f"{name}: {detail}")


def _status_check_one_mentor(slug: str, m: Mentor, r: _StatusReporter) -> None:
    """Validate a single mentor's corpus + backend config statically.

    Adds one or more rows to ``r.table`` and records problems / notes
    via the reporter's ``warn`` / ``fail`` helpers. No network probes.

    Args:
        slug: The mentor's slug (used in row labels).
        m: The :class:`Mentor` to validate.
        r: The reporter to record findings into.
    """
    corpus = load_playbooks(m.playbook_dir)
    backend_label = m.backend.kind if m.backend else "fallback"
    skill_count = sum(len(pb.skills) for pb in corpus)
    if not corpus:
        r.warn(f"  mentor {slug}", f"playbook_dir has no playbooks: {m.playbook_dir}")
    else:
        r.ok(
            f"  mentor {slug}",
            f"{len(corpus)} playbook(s), {skill_count} skill(s), backend={backend_label}",
        )
    if m.backend and m.backend.kind == "openclaw":
        env_name = m.backend.auth_bearer_env
        if env_name and not os.environ.get(env_name):
            r.warn(f"    {slug}.backend env", f"{env_name} is not set in the environment")
        if not re.match(r"^https?://", m.backend.url):
            r.fail(f"    {slug}.backend url", f"not http(s): {m.backend.url}")


def _status_check_mentors(mentors_root: Path, r: _StatusReporter) -> dict[str, Mentor]:
    """Validate the mentors registry directory and every mentor inside.

    Args:
        mentors_root: Directory expected to contain one ``<slug>/``
            subdirectory per mentor.
        r: The reporter to record findings into.

    Returns:
        The loaded mentor registry on success, or an empty dict when
        the directory was missing or the registry failed to load (the
        problem is also recorded on the reporter).
    """
    if not mentors_root.is_dir():
        r.fail("Mentors root", f"directory does not exist: {mentors_root}")
        return {}
    try:
        mentors = load_mentors(mentors_root)
    except Exception as e:
        r.fail("Mentor registry", f"failed to load: {e}")
        return {}
    if not mentors:
        r.warn("Mentor registry", f"no mentors under {mentors_root}")
    for slug, m in mentors.items():
        _status_check_one_mentor(slug, m, r)
    return mentors


def _status_check_mentees(mentees_file: Path, r: _StatusReporter) -> None:
    """Validate that the mentee allowlist file is present and parseable.

    Args:
        mentees_file: Path to the JSON allowlist.
        r: The reporter to record findings into. A missing file is
            treated as a warning (the operator may not have minted any
            mentees yet); a parse failure is a fatal problem.
    """
    if not mentees_file.exists():
        r.warn("Mentees file", f"does not exist yet: {mentees_file} (run `ammp mentee add` to mint one)")
        return
    try:
        mentees = load_mentees(mentees_file)
        r.ok("Mentees allowlist", f"{len(mentees)} mentee(s)")
    except Exception as e:
        r.fail("Mentees allowlist", f"failed to load: {e}")


def _status_check_audit_log(audit_log_path: Path, r: _StatusReporter) -> None:
    """Probe that the audit-log directory is writable from this process.

    Writes and deletes a small probe file next to ``audit_log_path``
    so the check is non-destructive. Any failure is recorded as fatal
    on the reporter — the server cannot honour AMMP §6.2 without a
    writable audit log.

    Args:
        audit_log_path: Where the audit log lives (parent dir is what
            actually gets probed).
        r: The reporter to record findings into.
    """
    try:
        audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        probe = audit_log_path.parent / ".ammp-status-probe"
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
        r.ok("Audit log writable", str(audit_log_path))
    except Exception as e:
        r.fail("Audit log writable", f"{audit_log_path}: {e}")


def _status_check_anthropic_key(s: Settings, mentors: dict[str, Mentor], r: _StatusReporter) -> None:
    """Warn when an anthropic-backed mentor is configured without an API key.

    No-op when no mentor uses the anthropic backend (explicit or
    fallback). Without the key, the global Anthropic backend
    degrades into the deterministic stub — operationally still
    valid, but worth flagging.

    Args:
        s: Settings holding the ``anthropic_api_key`` (or ``None``).
        mentors: The loaded mentor registry.
        r: The reporter to record the warning / OK row into.
    """
    has_anthropic_mentor = any(m.backend is None or m.backend.kind == "anthropic" for m in mentors.values())
    if not has_anthropic_mentor:
        return
    if not s.anthropic_api_key:
        r.warn(
            "AMMP_ANTHROPIC_API_KEY",
            "unset; AskMentor will return a deterministic stub for any anthropic-backed mentor",
        )
    else:
        r.ok("AMMP_ANTHROPIC_API_KEY", "set")
