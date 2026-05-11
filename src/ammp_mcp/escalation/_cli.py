"""`ammp escalation …` subcommands.

Operator surface for the mentor-mediated escalation flow:

* ``ammp escalation list`` — show every escalation in the JSONL
  with status / mentee / mentor / created-at.
* ``ammp escalation show <id>`` — full record for one id, including
  the question text and (if answered) the human mentor's reply.
* ``ammp escalation answer <id> "..."`` — manually inject the answer
  Z for an escalation. Useful when the configured delivery adapter
  is log-only (no inbound) and the operator wants to forward the
  human's reply by hand. Also handy as a test seam.
* ``ammp escalation cancel <id>`` — mark an escalation cancelled
  (e.g. B.a gave up out-of-band, the human can't be reached).

Manual ``answer`` / ``cancel`` write to the persistent store but
don't resolve any in-flight broker waiters — those live in the
running server's memory. To take effect for a B.a currently
blocking on ``EscalateToHumanMentor``, route the answer through
whatever inbound the live server has wired (Telegram bot, log
adapter manual inject, etc.). The CLI is best used for offline /
post-hoc work.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import typer
from rich.console import Console
from rich.table import Table

from .._cli_utils import wire_help_on_no_args
from ..settings import get_settings
from ._models import Escalation
from ._service import EscalationStore

console = Console()

escalation_app = typer.Typer(
    name="escalation",
    help="Inspect and manage mentor-mediated escalations.",
    add_completion=False,
)
wire_help_on_no_args(escalation_app)


def _open_store() -> EscalationStore:
    """Return the store bound to the active settings' escalations_file."""
    return EscalationStore(get_settings().escalations_file)


@escalation_app.command("list")
def escalation_list(
    status: str = typer.Option(
        "",
        "--status",
        help="Filter by status: pending | delivered | answered | cancelled | expired. Empty → all.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a Rich table."),
) -> None:
    """List escalations recorded by this server."""
    store = _open_store()
    rows = store.list_by_status(status or None)  # type: ignore[arg-type]
    if as_json:
        typer.echo(json.dumps([e.model_dump(mode="json") for e in rows], indent=2, ensure_ascii=False))
        return
    if not rows:
        console.print("[dim]No escalations recorded yet.[/dim]")
        return
    table = Table(title=f"Escalations ({len(rows)})")
    table.add_column("id", style="cyan", no_wrap=True)
    table.add_column("status", style="magenta")
    table.add_column("mentor", style="green")
    table.add_column("mentee", style="white")
    table.add_column("created", style="dim")
    table.add_column("question", style="white", overflow="fold")
    for e in rows:
        table.add_row(
            e.id[:8],
            e.status,
            e.mentor_slug,
            e.mentee_slug,
            e.created_at.isoformat(timespec="seconds"),
            e.question[:80] + ("…" if len(e.question) > 80 else ""),
        )
    console.print(table)


@escalation_app.command("show")
def escalation_show(escalation_id: str = typer.Argument(..., help="Full or short (8-hex) escalation id.")) -> None:
    """Print one escalation in detail."""
    store = _open_store()
    esc = _resolve_short_id(store, escalation_id)
    if esc is None:
        console.print(f"[red]Not found: {escalation_id!r}[/red]")
        raise typer.Exit(code=1)
    console.print_json(esc.model_dump_json(indent=2))


@escalation_app.command("answer")
def escalation_answer(
    escalation_id: str = typer.Argument(..., help="Escalation id (full or short)."),
    answer: str = typer.Argument(..., help="Answer text Z from A.h, to record on the escalation."),
) -> None:
    """Manually inject A.h's answer Z onto a pending escalation.

    Updates the persistent store; does NOT resolve any in-flight
    broker waiter in the running server. Use this when the delivery
    adapter is log-only, or for post-hoc records.
    """
    store = _open_store()
    esc = _resolve_short_id(store, escalation_id)
    if esc is None:
        console.print(f"[red]Not found: {escalation_id!r}[/red]")
        raise typer.Exit(code=1)
    if esc.status in {"answered", "cancelled", "expired"}:
        console.print(f"[yellow]Escalation already in terminal state {esc.status!r}; no change.[/yellow]")
        raise typer.Exit(code=2)
    updated = store.update(esc.id, status="answered", answered_at=datetime.now(UTC), answer=answer)
    if updated is None:
        console.print(f"[red]Update failed for {esc.id!r}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]✓[/green] Marked {esc.id[:8]} answered ({len(answer)} chars).")


@escalation_app.command("cancel")
def escalation_cancel(
    escalation_id: str = typer.Argument(..., help="Escalation id (full or short)."),
    reason: str = typer.Option("operator_cancelled", "--reason", help="Free-form note recorded on the escalation."),
) -> None:
    """Mark an escalation cancelled (e.g. B.a gave up, A.h unreachable)."""
    store = _open_store()
    esc = _resolve_short_id(store, escalation_id)
    if esc is None:
        console.print(f"[red]Not found: {escalation_id!r}[/red]")
        raise typer.Exit(code=1)
    if esc.status in {"answered", "cancelled", "expired"}:
        console.print(f"[yellow]Escalation already in terminal state {esc.status!r}; no change.[/yellow]")
        raise typer.Exit(code=2)
    updated = store.update(esc.id, status="cancelled", cancel_reason=reason)
    if updated is None:
        console.print(f"[red]Update failed for {esc.id!r}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]✓[/green] Cancelled {esc.id[:8]}: {reason}")


def _resolve_short_id(store: EscalationStore, raw: str) -> Escalation | None:
    """Resolve a full id or 8-hex prefix to an :class:`Escalation`.

    Args:
        store: The escalation store to search.
        raw: User-supplied id — full UUID4 hex, or the first 8 chars.

    Returns:
        The matching :class:`Escalation`, or ``None`` if zero or
        multiple matches.
    """
    target = raw.strip().lower()
    if not target:
        return None
    exact = store.get(target)
    if exact is not None:
        return exact
    candidates = [e for e in store.list_by_status() if e.id.startswith(target)]
    if len(candidates) == 1:
        return candidates[0]
    return None
