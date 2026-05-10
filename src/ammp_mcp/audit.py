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
    """Return an 8-hex-char hash of payload (or `—` for None / empty).
    The output is a stable identifier within a single server process —
    enough to correlate two log lines about the same request — but
    cannot be reversed back to plaintext."""
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
    """Append a single audit line. Thread-safe; opens + closes the file
    each call (small cost, big simplicity for a low-volume server)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    line = (
        f"{dt.datetime.now(dt.UTC).isoformat()} "
        f"op={operation} mentor={mentor} mentee={mentee} hash={request_hash}"
    )
    if extra:
        line += f" {extra}"
    line += "\n"
    with _lock, log_path.open("a", encoding="utf-8") as f:
        f.write(line)


def reset_salt_for_testing() -> None:
    """Test-only — re-roll the per-process salt so test runs are isolated."""
    global _salt
    _salt = os.urandom(16)
