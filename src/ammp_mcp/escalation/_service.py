"""Escalation persistence + in-memory broker.

Two cooperating pieces:

* :class:`EscalationStore` — append-only JSONL persistence of every
  escalation that has ever passed through this server. Survives
  restarts; status updates rewrite the file atomically.
* :class:`EscalationBroker` — in-process registry of
  ``asyncio.Event``s, keyed by escalation id. When a long-running
  tool handler awaits an answer it grabs a waiter from the broker;
  when the delivery adapter receives a reply it calls
  :meth:`EscalationBroker.resolve` to set the event. The broker
  also handles cancellation (B.a gave up, server shutdown).

Why split: the store has to be safe across processes (operator can
``cat`` the jsonl while the server runs); the broker is per-process
in-memory state. Keeping them separate makes the persistence layer
trivial to reason about + unit-test without an event loop.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from ._models import Escalation, EscalationStatus

logger = logging.getLogger(__name__)


class EscalationStore:
    """Append-only persistence of every escalation that crossed this server.

    Layout: one JSON object per line under ``<AMMP_DIR>/escalations.jsonl``.
    Status updates do not append a new line — they rewrite the whole
    file atomically (write to ``<path>.tmp``, ``os.replace`` over the
    original). The file is small (one line per escalation; even a busy
    deployment caps at a few hundred per day), so full rewrites are
    cheap and avoid the ambiguity of multi-line state machines.

    Thread-safe via an internal lock. Asyncio code calls these methods
    from the event-loop thread; the inbound delivery-adapter listener
    may be on a separate thread, hence the lock.

    Args:
        path: Filesystem path to the JSONL file. Created on the first
            write; reads handle absence as "empty store".
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def _read_all(self) -> list[Escalation]:
        """Read every escalation from disk. Returns ``[]`` when the file is absent."""
        if not self.path.is_file():
            return []
        out: list[Escalation] = []
        for raw in self.path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                out.append(Escalation.model_validate_json(line))
            except Exception as e:
                logger.warning("escalations.jsonl: skipping malformed line: %s", e)
        return out

    def _write_all(self, escalations: list[Escalation]) -> None:
        """Rewrite the jsonl atomically (write tmp, then ``os.replace``)."""
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tmp.open("w", encoding="utf-8") as f:
            for esc in escalations:
                # Pydantic's `model_dump_json` serialises datetimes as
                # ISO 8601 with timezone — JSON-portable, round-trippable
                # via `model_validate_json`. ensure_ascii is False so
                # human-language questions survive intact.
                f.write(esc.model_dump_json() + "\n")
        tmp.replace(self.path)

    def append(self, esc: Escalation) -> None:
        """Persist a freshly-created escalation. O(n) due to atomic rewrite."""
        with self._lock:
            existing = self._read_all()
            existing.append(esc)
            self._write_all(existing)

    def update(self, escalation_id: str, **changes: object) -> Escalation | None:
        """Apply partial updates to one escalation, return the new record.

        Args:
            escalation_id: The id of the escalation to mutate.
            **changes: Fields to overwrite (e.g. ``status="answered"``,
                ``answer="..."``, ``answered_at=datetime.now(UTC)``).

        Returns:
            The updated :class:`Escalation`, or ``None`` if no
            escalation with that id exists.
        """
        with self._lock:
            existing = self._read_all()
            updated: Escalation | None = None
            new_list: list[Escalation] = []
            for e in existing:
                if e.id == escalation_id:
                    merged = e.model_copy(update=changes)
                    updated = merged
                    new_list.append(merged)
                else:
                    new_list.append(e)
            if updated is None:
                return None
            self._write_all(new_list)
            return updated

    def get(self, escalation_id: str) -> Escalation | None:
        """Return one escalation by id, or ``None`` if unknown."""
        with self._lock:
            for e in self._read_all():
                if e.id == escalation_id:
                    return e
            return None

    def list_by_status(self, status: EscalationStatus | None = None) -> list[Escalation]:
        """Return all escalations, optionally filtered by status."""
        with self._lock:
            rows = self._read_all()
        if status is None:
            return rows
        return [e for e in rows if e.status == status]


class _Waiter:
    """One pending await — an Event plus the answer slot it'll be filled with."""

    __slots__ = ("answer", "cancel_reason", "event")

    def __init__(self) -> None:
        self.event = asyncio.Event()
        self.answer: str | None = None
        self.cancel_reason: str | None = None


class EscalationBroker:
    """In-process registry of asyncio waiters keyed by escalation id.

    The long-running ``EscalateToHumanMentor`` tool handler registers a
    waiter via :meth:`wait_for_answer`; the delivery adapter calls
    :meth:`resolve` from its inbound thread / task once A.h replies.
    The handler's `await waiter.event.wait()` returns and the tool
    call completes.

    Lifecycle:
    - ``open(id)`` — handler creates a waiter slot at the start of
      the call.
    - ``resolve(id, answer)`` — delivery adapter wakes the waiter.
    - ``cancel(id, reason)`` — handler or shutdown path wakes the
      waiter with no answer + a reason.

    State is lost across server restarts; pending waiters are dropped
    and the corresponding store entries get marked ``expired`` by the
    boot path so they don't dangle forever.
    """

    def __init__(self) -> None:
        self._waiters: dict[str, _Waiter] = {}
        self._lock = threading.Lock()

    @staticmethod
    def new_id() -> str:
        """Mint a fresh escalation id. UUID4 hex, no dashes."""
        return uuid.uuid4().hex

    def open(self, escalation_id: str) -> _Waiter:
        """Register a waiter for this escalation id.

        Args:
            escalation_id: The id to wait on.

        Returns:
            The :class:`_Waiter`. Caller awaits ``waiter.event`` then
            reads ``waiter.answer`` or ``waiter.cancel_reason``.

        Raises:
            RuntimeError: If a waiter for this id already exists
                (shouldn't happen — ids are freshly minted per call).
        """
        with self._lock:
            if escalation_id in self._waiters:
                raise RuntimeError(f"escalation {escalation_id} already has a waiter")
            waiter = _Waiter()
            self._waiters[escalation_id] = waiter
            return waiter

    def resolve(self, escalation_id: str, answer: str) -> bool:
        """Wake the waiter with the human mentor's answer.

        Safe to call from any thread — uses
        :meth:`asyncio.AbstractEventLoop.call_soon_threadsafe` to set
        the event from non-loop contexts.

        Args:
            escalation_id: The id whose waiter should resolve.
            answer: A.h's reply Z.

        Returns:
            True when the waiter was found and woken; False when no
            waiter exists for this id (e.g. tool call already
            cancelled, or this is a stray late delivery).
        """
        with self._lock:
            waiter = self._waiters.pop(escalation_id, None)
        if waiter is None:
            return False
        waiter.answer = answer
        # asyncio.Event.set is not thread-safe; route through the loop.
        try:
            loop = asyncio.get_event_loop()
            loop.call_soon_threadsafe(waiter.event.set)
        except RuntimeError:
            # No running loop in this thread — fall back to direct set,
            # acceptable because callers in tests use the same loop.
            waiter.event.set()
        return True

    def cancel(self, escalation_id: str, reason: str) -> bool:
        """Wake the waiter without an answer, signalling cancellation."""
        with self._lock:
            waiter = self._waiters.pop(escalation_id, None)
        if waiter is None:
            return False
        waiter.cancel_reason = reason
        try:
            loop = asyncio.get_event_loop()
            loop.call_soon_threadsafe(waiter.event.set)
        except RuntimeError:
            waiter.event.set()
        return True

    def release(self, escalation_id: str) -> bool:
        """Drop the waiter without setting an answer or cancel reason.

        Used by the sync-or-pending flow: when ``EscalateToHumanMentor``
        returns ``status="pending"`` to the mentee, the original waiter
        is no longer attached to a live MCP call — releasing it frees
        the slot so a later ``GetEscalation`` can arm a fresh waiter
        for the same id.

        Args:
            escalation_id: Id of the escalation to release.

        Returns:
            ``True`` when a waiter was found and dropped, ``False``
            when none was registered (already resolved / cancelled /
            never opened).
        """
        with self._lock:
            return self._waiters.pop(escalation_id, None) is not None

    def has_waiter(self, escalation_id: str) -> bool:
        """True if a waiter is currently registered for this id."""
        with self._lock:
            return escalation_id in self._waiters

    def pending_ids(self) -> list[str]:
        """Snapshot of every id with an active waiter — for tests + diagnostics."""
        with self._lock:
            return list(self._waiters.keys())


def expire_orphaned_pending(store: EscalationStore) -> int:
    """Mark every still-``pending`` or ``delivered`` row as ``expired``.

    Called from server boot, on the theory that any escalation in
    pending/delivered state at startup is orphaned — its waiter
    lived in the previous process and is gone. Without this, the
    jsonl would carry zombie entries forever.

    Args:
        store: The persistence store to scan.

    Returns:
        Count of rows transitioned to ``expired``.
    """
    expired = 0
    for esc in store.list_by_status():
        if esc.status in {"pending", "delivered"}:
            store.update(esc.id, status="expired", cancel_reason="server_restart")
            expired += 1
    if expired:
        logger.info("escalations: marked %d orphaned pending/delivered as expired on boot", expired)
    return expired


def new_escalation(
    *,
    mentor_slug: str,
    mentee_slug: str,
    question: str,
    context: str | None = None,
) -> Escalation:
    """Construct a fresh :class:`Escalation` with a new id + timestamp.

    Args:
        mentor_slug: A.a's slug.
        mentee_slug: B.a's slug (from the auth path).
        question: The (already B.h-approved) question text X.
        context: Optional context the mentee chose to attach.

    Returns:
        A new pending :class:`Escalation` — caller persists it via
        :meth:`EscalationStore.append` and opens a waiter via
        :meth:`EscalationBroker.open`.
    """
    return Escalation(
        id=EscalationBroker.new_id(),
        mentor_slug=mentor_slug,
        mentee_slug=mentee_slug,
        question=question,
        context=context,
        status="pending",
        created_at=datetime.now(UTC),
    )
