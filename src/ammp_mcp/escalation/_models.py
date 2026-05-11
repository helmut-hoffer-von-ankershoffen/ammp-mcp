"""Escalation pydantic model + status enum.

One ``Escalation`` represents one B.a → A.h question routed by A.a.
Stored as one JSON line in ``~/.ammp/escalations.jsonl`` (append-only;
status updates rewrite the whole file).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EscalationStatus = Literal["pending", "delivered", "answered", "cancelled", "expired"]


class Escalation(BaseModel):
    """One escalation from an agentic mentee to a human mentor.

    Attributes:
        id: UUID4 generated at create time; the on-the-wire id used
            for cancellation, log correlation, and adapter threading.
        mentor_slug: The agentic mentor (A.a) the escalation is on.
        mentee_slug: The agentic mentee (B.a) that originated it.
        question: The (possibly B.h-approved) question text X.
        context: Optional extra context the mentee chose to attach.
        status: Lifecycle state — see :data:`EscalationStatus`.
        created_at: When B.a's call landed.
        delivered_at: When the delivery adapter reported the message
            reached A.h's channel (e.g. Telegram message_id assigned),
            or ``None`` if delivery has not happened yet.
        answered_at: When A.h's reply landed back on the server.
        answer: The human mentor's reply Z, or ``None`` while pending.
        delivery_ref: Adapter-specific reference for threading replies.
            For the Telegram adapter, this is the outbound message_id.
        cancel_reason: Free-form note when status is ``cancelled`` or
            ``expired`` (server restart, B.a disconnected, timeout).
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    mentor_slug: str
    mentee_slug: str
    question: str = Field(min_length=1, max_length=8000)
    context: str | None = Field(default=None, max_length=8000)
    status: EscalationStatus = "pending"
    created_at: datetime
    delivered_at: datetime | None = None
    answered_at: datetime | None = None
    answer: str | None = Field(default=None, max_length=20000)
    delivery_ref: str | None = None
    cancel_reason: str | None = None
