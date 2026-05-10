"""Mentor and mentee registries.

A *mentor* is a directory under `mentors_root` containing `mentor.json`
(metadata) and `playbooks/*.md` (corpus). A *mentee* is an entry in
`mentees_file` (JSON list of objects). Both are loaded once at server
boot; reload via the CLI or by sending the server SIGHUP.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from .models import Mentee, Mentor

logger = logging.getLogger(__name__)


# ─── Mentor registry ──────────────────────────────────────────────────────


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


# ─── Mentee registry ──────────────────────────────────────────────────────


def load_mentees(path: Path) -> dict[str, Mentee]:
    """Load mentees.json. The file is a list of mentee objects keyed by slug."""
    if not path.is_file():
        logger.warning("mentees_file %s does not exist; returning empty allowlist", path)
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"mentees file must be a JSON list, got {type(raw).__name__}")
    out: dict[str, Mentee] = {}
    for entry in raw:
        m = Mentee(**entry)
        out[m.slug] = m
    return out


def save_mentees(path: Path, mentees: dict[str, Mentee]) -> None:
    """Persist the mentee allowlist back to disk (used by the CLI)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    serialised = [m.model_dump() for m in mentees.values()]
    path.write_text(json.dumps(serialised, indent=2) + "\n", encoding="utf-8")


def hash_api_key(api_key: str) -> str:
    """Hex sha256 of the Bearer token.

    Plain SHA-256 is appropriate here — *not* a slow KDF (bcrypt /
    argon2 / scrypt). API keys are minted by ``ammp mentee add`` as
    ``"ammp-" + secrets.token_urlsafe(32)``: 32 bytes (256 bits) of
    cryptographically secure randomness from the OS CSPRNG. Brute-
    forcing a 256-bit-entropy preimage of a 256-bit hash is
    computationally infeasible regardless of the hash's cost factor;
    the slow-KDF protections that matter for *human-chosen* passwords
    are irrelevant for high-entropy random tokens. Same convention as
    PyPI tokens, Stripe API keys, GitHub PATs, etc.

    (CodeQL's `py/weak-cryptographic-algorithm` rule flags this
    because it can't see the input-entropy assumption — see the
    repo's Code Scanning dismissals for the rationale.)
    """
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def find_mentee_by_api_key(mentees: dict[str, Mentee], api_key: str) -> Mentee | None:
    """Resolve a Bearer key to a mentee record.

    Hashes the incoming key once, then looks up the hash in the
    allowlist. Constant-time-ish comparison is good enough here
    because the hash is computed before any equality test, and the
    only comparison is between two short fixed-size hex strings.

    Args:
        mentees: Loaded mentee allowlist.
        api_key: Plaintext Bearer token from the request.

    Returns:
        The matching mentee, or ``None`` if no match.
    """
    h = hash_api_key(api_key)
    for m in mentees.values():
        if m.api_key_hash == h:
            return m
    return None
