"""Hash-only audit-log aggregation — backs `ammp system usage`."""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from rich.table import Table

_AUDIT_LINE_RE = re.compile(
    r"^(?P<ts>\S+)\s+op=(?P<op>\S+)\s+mentor=(?P<mentor>\S+)\s+mentee=(?P<mentee>\S+)\s+hash=(?P<hash>\S+)"
)


@dataclass
class _AuditAggregate:
    op_counts: Counter[str] = field(default_factory=Counter)
    mentor_counts: Counter[str] = field(default_factory=Counter)
    mentee_counts: Counter[str] = field(default_factory=Counter)
    parsed: int = 0
    skipped: int = 0
    earliest: dt.datetime | None = None
    latest: dt.datetime | None = None


def _usage_parse_one_line(line: str, cutoff: dt.datetime | None, agg: _AuditAggregate) -> None:
    m = _AUDIT_LINE_RE.match(line)
    if not m:
        agg.skipped += 1
        return
    try:
        ts = dt.datetime.fromisoformat(m.group("ts"))
    except ValueError:
        agg.skipped += 1
        return
    if cutoff and ts < cutoff:
        return
    agg.parsed += 1
    agg.op_counts[m.group("op")] += 1
    agg.mentor_counts[m.group("mentor")] += 1
    agg.mentee_counts[m.group("mentee")] += 1
    if agg.earliest is None or ts < agg.earliest:
        agg.earliest = ts
    if agg.latest is None or ts > agg.latest:
        agg.latest = ts


def _usage_parse_audit(path: Path, cutoff: dt.datetime | None) -> _AuditAggregate:
    agg = _AuditAggregate()
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            _usage_parse_one_line(line, cutoff, agg)
    return agg


def _usage_render_breakdown(title: str, counts: Counter[str], col_label: str, style: str) -> Table:
    t = Table(title=title)
    t.add_column(col_label, style="white")
    t.add_column("count", justify="right", style=style)
    for k, n in counts.most_common():
        t.add_row(k, str(n))
    return t
