"""Typer-based CLI for housekeeping.

Subject-then-action layout (mirrors `git remote list`, `kubectl get pods`):

    ammp mentor list
    ammp mentee list / add / remove / rotate-key / check-key
    ammp playbook list / show
    ammp system setup / status / health / usage / capability / serve

Top-level shortcuts for the most common system verbs:

    ammp serve       → ammp system serve
    ammp setup       → ammp system setup
    ammp status      → ammp system status
    ammp health      → ammp system health
    ammp usage       → ammp system usage
    ammp capability  → ammp system capability
"""

from __future__ import annotations

import typer

from ._cli_utils import wire_help_on_no_args
from .mentee._cli import mentee_app
from .mentor._cli import mentor_app
from .playbook._cli import playbook_app
from .system._cli import (
    capability,
    health,
    serve,
    setup,
    status,
    system_app,
    usage,
)

app = typer.Typer(
    name="ammp",
    help="Housekeeping CLI for the AMMP Mentoring-track reference server.",
    add_completion=False,
)
wire_help_on_no_args(app)
app.add_typer(mentor_app)
app.add_typer(mentee_app)
app.add_typer(playbook_app)
app.add_typer(system_app)

# Top-level aliases for the most common system verbs. Canonical home
# stays on `ammp system <verb>`; these are shortcuts for muscle memory.
for _name, _fn in (
    ("serve", serve),
    ("setup", setup),
    ("status", status),
    ("health", health),
    ("usage", usage),
    ("capability", capability),
):
    app.command(_name, help=f"Alias for `ammp system {_name}`.")(_fn)


if __name__ == "__main__":
    app()
