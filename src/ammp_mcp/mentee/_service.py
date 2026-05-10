"""Mentee allowlist — load/save the JSON file and resolve API keys."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from ._models import Mentee

logger = logging.getLogger(__name__)


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
    path.write_text(json.dumps(serialised, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


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
