"""Mentor registry — discover mentor directories and load their metadata.

A *mentor* is a directory under `mentors_root` containing `mentor.json`
(metadata) and `playbooks/*.md` (corpus). Loaded once at server boot;
reload via the CLI or by sending the server SIGHUP.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ._models import Mentor

logger = logging.getLogger(__name__)


def load_mentors(root: Path) -> dict[str, Mentor]:
    """Discover mentors under ``root``.

    Each subdirectory of ``root`` is a mentor; its ``mentor.json``
    describes it; its ``playbooks/`` (or ``*.md`` files in the mentor
    dir itself) are the corpus.

    Args:
        root: Root directory holding one subdir per mentor.

    Returns:
        Mapping of mentor slug → :class:`Mentor`. Empty dict when
        ``root`` does not exist.
    """
    if not root.is_dir():
        logger.warning("mentors_root %s does not exist; returning empty registry", root)
        return {}
    mentors: dict[str, Mentor] = {}
    for mentor_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        meta_file = mentor_dir / "mentor.json"
        if not meta_file.is_file():
            logger.warning("skipping %s — no mentor.json", mentor_dir)
            continue
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        # JSON has no comment syntax — treat underscore-prefixed top-level
        # keys (`_note`, `_backend_example`, …) as documentation and drop
        # them before validation. Lets operators leave inline notes in
        # mentor.json without tripping the strict pydantic schema.
        meta = {k: v for k, v in meta.items() if not k.startswith("_")}
        # Default playbook_dir is `<mentor>/playbooks/` if it exists, else `<mentor>/`.
        candidate_playbook_dirs = [mentor_dir / "playbooks", mentor_dir]
        chosen_playbook_dir = next((d for d in candidate_playbook_dirs if d.is_dir()), mentor_dir)
        meta.setdefault("playbook_dir", str(chosen_playbook_dir))
        meta.setdefault("slug", mentor_dir.name)
        mentor = Mentor(**meta)
        mentors[mentor.slug] = mentor
    return mentors


def get_mentor(mentors: dict[str, Mentor], slug: str | None, default_slug: str) -> Mentor | None:
    """Resolve a mentor slug to a :class:`Mentor`.

    Empty or ``None`` ``slug`` routes to the configured default mentor.

    Args:
        mentors: Loaded mentor registry.
        slug: Caller-supplied slug, possibly empty / None.
        default_slug: Server's default mentor slug.

    Returns:
        The matching mentor, or ``None`` if no match.
    """
    chosen = (slug or default_slug).strip().lower()
    return mentors.get(chosen)
