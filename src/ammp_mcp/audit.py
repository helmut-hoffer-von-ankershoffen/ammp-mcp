"""Hash-only audit log (AMMP §6.2).

Append-only, plaintext-safe. Each line records timestamp + operation +
opaque request hash + opaque mentor + mentee identifiers. The plaintext
content of any request payload is never written. The hash domain is
process-private — colons of `q:abcd1234` between entries are NOT
collidable across runs (we mix in a per-process random salt at boot).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import secrets
from pathlib import Path
from threading import Lock

_lock = Lock()
_salt = secrets.token_bytes(16)  # per-process; cleared on restart


def short_hash(payload: str | bytes | None) -> str:
    """Return an 8-hex-char hash of ``payload``.

    The output is a stable identifier within a single server process —
    enough to correlate two log lines about the same request — but
    cannot be reversed back to plaintext.

    Args:
        payload: The bytes (or str, encoded as UTF-8) to hash. ``None``
            or empty returns the placeholder ``"—"``.

    Returns:
        Lowercase 8-character hex string when payload is non-empty;
        ``"—"`` for None / empty payloads.
    """
    if not payload:
        return "—"
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return hashlib.sha256(_salt + payload).hexdigest()[:8]


def log_event(
    log_path: Path,
    operation: str,
    *,
    mentor: str = "—",
    mentee: str = "—",
    request_hash: str = "—",
    extra: str | None = None,
) -> None:
    """Append a single audit line to ``log_path``.

    Thread-safe; opens and closes the file each call (small cost, big
    simplicity for a low-volume server).

    Args:
        log_path: The audit-log file. Parent dirs are created on demand.
        operation: AMMP operation name (e.g. ``"AskMentor"``).
        mentor: Mentor slug (default ``"—"``).
        mentee: Mentee slug (default ``"—"``).
        request_hash: Opaque request hash from :func:`short_hash`.
        extra: Optional free-form trailer (kept short; never include
            request payloads).
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    line = f"{dt.datetime.now(dt.UTC).isoformat()} op={operation} mentor={mentor} mentee={mentee} hash={request_hash}"
    if extra:
        line += f" {extra}"
    line += "\n"
    with _lock, log_path.open("a", encoding="utf-8") as f:
        f.write(line)


def reset_salt_for_testing() -> None:
    """Re-roll the per-process salt so test runs are isolated.

    Test-only helper. Production code never calls this — the salt is
    fixed for the lifetime of the process so that hashes within a
    single run are correlatable.
    """
    global _salt
    _salt = os.urandom(16)
