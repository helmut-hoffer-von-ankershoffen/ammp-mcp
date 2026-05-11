"""Playbook corpus loader and search.

A mentor's corpus is a two-level hierarchy:

* **Playbook** — an *area of practice*. One subdirectory under
  ``<mentor_dir>/playbooks/<slug>/``, with a ``playbook.json``
  carrying the playbook's name + description.
* **WorkInstruction** — one fine-grained craft rule. One markdown
  file under the playbook's directory. The first ``# H1`` is the
  instruction's title; the first non-blank, non-heading paragraph
  after it is the summary.

This split tracks the conceptual model: an AMMP playbook describes
*what a mentor knows how to do* (an area of practice), and a work
instruction is *one specific rule inside that area*. AskMentor
prompts get the whole corpus flattened across playbooks.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WorkInstruction:
    """One work instruction loaded from a markdown file inside a playbook.

    Attributes:
        id: Filename stem (the on-the-wire id used by
            ``GetWorkInstruction``).
        title: First H1 in the file (or the stem if no H1 is present).
        summary: First non-blank, non-heading line, ~200-char truncated.
        body: Full markdown source.
        path: Filesystem path the instruction was loaded from.
        playbook_id: Slug of the playbook this instruction belongs to.
    """

    id: str
    title: str
    summary: str
    body: str
    path: Path
    playbook_id: str


@dataclass(frozen=True, slots=True)
class Playbook:
    """One playbook — an area of practice. Loaded from a subdirectory.

    Attributes:
        id: Directory name (slug) — the on-the-wire id used by
            ``GetPlaybook``.
        name: Human-readable display name from ``playbook.json``.
        description: One-line mentee-facing summary from
            ``playbook.json``.
        dir: Filesystem path to the playbook directory.
        instructions: Work instructions loaded from ``*.md`` siblings of
            the playbook's ``playbook.json``, sorted by filename.
    """

    id: str
    name: str
    description: str
    dir: Path
    instructions: list[WorkInstruction] = field(default_factory=list)


# `[^\n]+` instead of `.+` so the title group never backtracks across
# lines (avoids the catastrophic-backtracking class S5852 flags). Same
# match semantics with re.MULTILINE — the title is whatever follows
# `# ` on a single line.
_TITLE_RE = re.compile(r"^#\s+([^\n]+)$", re.MULTILINE)
# Bounded quantifier (`{0,32768}?`) avoids the unbounded backtracking
# class S5852 still flags. Frontmatter on a real instruction is well
# under 32 KB; capping the quantifier keeps the pattern matching the
# same set of inputs we care about while making catastrophic
# backtracking impossible by construction.
_FRONTMATTER_RE = re.compile(r"^---\n[\s\S]{0,32768}?\n---\n")

_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


def _summarise(body: str) -> str:
    """Return the first non-blank, non-heading line, truncated to ~200 chars."""
    for line in body.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            return s[:200] + ("…" if len(s) > 200 else "")
    return ""


def _load_one_instruction(path: Path, playbook_id: str) -> WorkInstruction:
    """Read one markdown file into a :class:`WorkInstruction`.

    Strips an optional YAML frontmatter block, derives the title from
    the first ``# `` heading (falling back to the filename stem), and
    summarises the first non-heading line for the AMMP response
    envelope.

    Args:
        path: Filesystem path to a single ``*.md`` instruction file.
        playbook_id: Slug of the playbook this instruction belongs to.

    Returns:
        The fully-loaded :class:`WorkInstruction` with ``id`` =
        filename stem and ``body`` = the full original text
        (frontmatter included).
    """
    text = path.read_text(encoding="utf-8")
    text_no_frontmatter = _FRONTMATTER_RE.sub("", text, count=1)
    title_match = _TITLE_RE.search(text_no_frontmatter)
    title = title_match.group(1).strip() if title_match else path.stem
    body_after_title = text_no_frontmatter[title_match.end() :] if title_match else text_no_frontmatter
    summary = _summarise(body_after_title)
    return WorkInstruction(
        id=path.stem,
        title=title,
        summary=summary,
        body=text,
        path=path,
        playbook_id=playbook_id,
    )


def _load_one_playbook(playbook_dir: Path) -> Playbook | None:
    """Load one playbook directory into a :class:`Playbook`.

    Reads ``playbook.json`` for the playbook's display name +
    description, then loads every sibling ``*.md`` as a work
    instruction. ``README.md`` is excluded.

    Args:
        playbook_dir: Directory containing ``playbook.json`` and one
            ``*.md`` file per work instruction.

    Returns:
        The loaded :class:`Playbook`, or ``None`` if the directory has
        no ``playbook.json`` (in which case it's not a playbook and is
        skipped by the loader).
    """
    meta_file = playbook_dir / "playbook.json"
    if not meta_file.is_file():
        return None
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    name = str(meta.get("name") or playbook_dir.name)
    description = str(meta.get("description") or "")
    pid = playbook_dir.name
    instructions = [
        _load_one_instruction(p, pid) for p in sorted(playbook_dir.glob("*.md")) if p.name.lower() != "readme.md"
    ]
    return Playbook(id=pid, name=name, description=description, dir=playbook_dir, instructions=instructions)


def load_playbooks(playbook_root: Path) -> list[Playbook]:
    """Scan a mentor's ``playbooks/`` directory and load each playbook.

    Each subdirectory containing a ``playbook.json`` is a playbook;
    every ``*.md`` next to it is one work instruction in that
    playbook. Subdirectories without ``playbook.json`` are skipped
    (with a warning) so a mistyped layout never silently swallows
    content into the wrong shape.

    Args:
        playbook_root: Mentor's ``playbooks/`` directory.

    Returns:
        Playbooks sorted by directory name. Empty list when
        ``playbook_root`` does not exist.
    """
    if not playbook_root.is_dir():
        return []
    out: list[Playbook] = []
    for sub in sorted(p for p in playbook_root.iterdir() if p.is_dir()):
        if not _SLUG_RE.match(sub.name):
            continue
        pb = _load_one_playbook(sub)
        if pb is None:
            logger.warning("skipping %s — no playbook.json", sub)
            continue
        out.append(pb)
    return out


def flatten_instructions(corpus: list[Playbook]) -> list[WorkInstruction]:
    """Flatten a playbook corpus into a single list of work instructions.

    Used by ``AskMentor`` to feed every instruction across every
    playbook into the keyword ranker, and by the audit/search paths
    that want to operate on instruction granularity.

    Args:
        corpus: Playbooks to flatten.

    Returns:
        Every :class:`WorkInstruction` from every playbook, in playbook
        order then file order.
    """
    return [wi for pb in corpus for wi in pb.instructions]


def safe_id(raw: str) -> str | None:
    """Validate a playbook or instruction id from untrusted mentee input.

    Args:
        raw: Caller-supplied id string.

    Returns:
        Canonical slug, or ``None`` when rejected (path-traversal
        sequences, dotfile prefix, empty, longer than 128 chars).
    """
    s = raw.strip()
    if not s or "/" in s or "\\" in s or s.startswith(".") or len(s) > 128:
        return None
    return s


def search(corpus: list[Playbook], query: str, limit: int = 5) -> list[tuple[WorkInstruction, int, str]]:
    """Substring-rank a corpus for a query string, returning instructions.

    Searches across every work instruction in every playbook. Results
    carry their ``playbook_id`` via the ``WorkInstruction`` itself, so
    callers can render hits in their playbook context without an
    extra lookup.

    Args:
        corpus: Playbooks to search.
        query: User-supplied query string. Empty / whitespace returns
            an empty list.
        limit: Maximum number of matches to return.

    Returns:
        ``(instruction, rank, snippet)`` triples sorted by descending
        rank.
    """
    q = (query or "").strip().lower()
    if not q:
        return []
    rows: list[tuple[WorkInstruction, int, str]] = []
    for wi in flatten_instructions(corpus):
        body_lower = wi.body.lower()
        idx = body_lower.find(q)
        if idx < 0:
            continue
        rank = body_lower.count(q)
        start = max(0, idx - 80)
        end = min(len(wi.body), idx + len(q) + 80)
        snippet = wi.body[start:end].replace("\n", " ").strip()
        if start > 0:
            snippet = "…" + snippet
        if end < len(wi.body):
            snippet = snippet + "…"
        rows.append((wi, rank, snippet))
    rows.sort(key=lambda r: -r[1])
    return rows[:limit]


def keyword_rank(corpus: list[Playbook], question: str, limit: int = 3) -> list[tuple[WorkInstruction, int]]:
    """Token-rank a corpus against a question for cheap retrieval.

    Used by ``AskMentor`` to feed the top-N most relevant work
    instructions into the backend's prompt. Stopwords are filtered.

    Args:
        corpus: Playbooks to score.
        question: The mentee's question.
        limit: Maximum number of matches to return.

    Returns:
        ``(instruction, rank)`` pairs sorted by descending rank.
    """
    stopwords = {
        "the", "and", "for", "with", "what", "when", "how", "why", "you",
        "are", "this", "that", "from", "into", "over", "about", "your",
        "have", "has", "had", "was", "were", "will", "can", "could",
        "should", "would", "but", "not", "any", "all", "out", "off",
    }  # fmt: skip
    tokens = [t for t in re.findall(r"[a-z][a-z0-9-]+", question.lower()) if len(t) >= 3 and t not in stopwords]
    if not tokens:
        return []
    rows: list[tuple[WorkInstruction, int]] = []
    for wi in flatten_instructions(corpus):
        body_lower = wi.body.lower()
        rank = sum(body_lower.count(t) for t in tokens)
        if rank > 0:
            rows.append((wi, rank))
    rows.sort(key=lambda r: -r[1])
    return rows[:limit]
