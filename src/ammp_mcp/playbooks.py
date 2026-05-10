"""Playbook corpus loader and search.

A *playbook* is a markdown file under a mentor's playbook directory.
The first H1 in the file is the title; the first non-blank, non-heading
paragraph after it is the summary. README.md is excluded (it's the index,
not a playbook). Filenames without the `.md` extension are the ids.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Playbook:
    id: str
    title: str
    summary: str
    body: str
    path: Path


# `[^\n]+` instead of `.+` so the title group never backtracks across
# lines (avoids the catastrophic-backtracking class S5852 flags). Same
# match semantics with re.MULTILINE — the title is whatever follows
# `# ` on a single line.
_TITLE_RE = re.compile(r"^#\s+([^\n]+)$", re.MULTILINE)
# Bounded `[\s\S]*?` instead of `.*?` for the same reason — non-greedy
# but with explicit any-char including newlines.
_FRONTMATTER_RE = re.compile(r"^---\n[\s\S]*?\n---\n")


def _summarise(body: str) -> str:
    """Return the first non-blank, non-heading line, truncated to ~200 chars."""
    for line in body.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            return s[:200] + ("…" if len(s) > 200 else "")
    return ""


def _load_one(path: Path) -> Playbook:
    text = path.read_text(encoding="utf-8")
    text_no_frontmatter = _FRONTMATTER_RE.sub("", text, count=1)
    title_match = _TITLE_RE.search(text_no_frontmatter)
    title = title_match.group(1).strip() if title_match else path.stem
    body_after_title = text_no_frontmatter[title_match.end() :] if title_match else text_no_frontmatter
    summary = _summarise(body_after_title)
    return Playbook(id=path.stem, title=title, summary=summary, body=text, path=path)


def load_corpus(directory: Path) -> list[Playbook]:
    """Scan a directory for `*.md` files (excluding README.md) and load
    them sorted by filename. Returns an empty list if the directory does
    not exist."""
    if not directory.is_dir():
        return []
    return [_load_one(p) for p in sorted(directory.glob("*.md")) if p.name.lower() != "readme.md"]


def safe_id(raw: str) -> str | None:
    """Validate a playbook id from untrusted input. Returns the canonical
    slug or None if rejected (path traversal, dotfile, empty)."""
    s = raw.strip()
    if not s or "/" in s or "\\" in s or s.startswith(".") or len(s) > 128:
        return None
    return s


def search(corpus: list[Playbook], query: str, limit: int = 5) -> list[tuple[Playbook, int, str]]:
    """Substring-rank a corpus for a query. Returns (playbook, rank, snippet)
    tuples sorted by descending rank. v0.2 implementation; v0.3 will add
    embedding-based ranking."""
    q = (query or "").strip().lower()
    if not q:
        return []
    rows: list[tuple[Playbook, int, str]] = []
    for pb in corpus:
        body_lower = pb.body.lower()
        idx = body_lower.find(q)
        if idx < 0:
            continue
        rank = body_lower.count(q)
        start = max(0, idx - 80)
        end = min(len(pb.body), idx + len(q) + 80)
        snippet = pb.body[start:end].replace("\n", " ").strip()
        if start > 0:
            snippet = "…" + snippet
        if end < len(pb.body):
            snippet = snippet + "…"
        rows.append((pb, rank, snippet))
    rows.sort(key=lambda r: -r[1])
    return rows[:limit]


def keyword_rank(corpus: list[Playbook], question: str, limit: int = 3) -> list[tuple[Playbook, int]]:
    """Token-ranked match across the corpus, used as cheap retrieval for
    AskMentor. Returns (playbook, rank) sorted by descending rank."""
    stopwords = {
        "the", "and", "for", "with", "what", "when", "how", "why", "you",
        "are", "this", "that", "from", "into", "over", "about", "your",
        "have", "has", "had", "was", "were", "will", "can", "could",
        "should", "would", "but", "not", "any", "all", "out", "off",
    }  # fmt: skip
    tokens = [t for t in re.findall(r"[a-z][a-z0-9-]+", question.lower()) if len(t) >= 3 and t not in stopwords]
    if not tokens:
        return []
    rows: list[tuple[Playbook, int]] = []
    for pb in corpus:
        body_lower = pb.body.lower()
        rank = sum(body_lower.count(t) for t in tokens)
        if rank > 0:
            rows.append((pb, rank))
    rows.sort(key=lambda r: -r[1])
    return rows[:limit]
