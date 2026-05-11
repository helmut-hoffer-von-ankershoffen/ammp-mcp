"""Escalation-to-human-mentor module.

A mentor-mediated escalation lets an agentic mentee (B.a) ask the
human standing behind an agentic mentor (A.h) a question that A.a
itself can't answer with confidence. The flow:

1. B.a calls ``EscalateToHumanMentor(question)`` on A.a.
2. A.a persists the escalation, hands the question to a delivery
   adapter (Telegram bot, OpenClaw bridge, etc.), and awaits the
   answer on an in-memory ``asyncio.Event``.
3. A.h reads the question on whatever channel the adapter delivers
   to, replies; the adapter inbound-routes the reply back to the
   server which resolves the waiter.
4. The original MCP tool call returns the answer Z to B.a.

Cross-compartment consent (B.h must approve forwarding X to A.h)
lives on the mentee side, not here — A.a publishes a recommended
draft in ``AskMentor`` responses and trusts B.a to gate on B.h.

Per AMMP §3.4 the agentic mentor never reaches across compartments
unilaterally; this module is the mechanism for the consented case.
"""

from __future__ import annotations

from ._models import Escalation, EscalationStatus
from ._service import EscalationBroker, EscalationStore

__all__ = [
    "Escalation",
    "EscalationBroker",
    "EscalationStatus",
    "EscalationStore",
]
