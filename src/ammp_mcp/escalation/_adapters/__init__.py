"""Delivery adapters for escalations to the human mentor.

Each adapter implements one side: outbound delivery of a question X
to A.h's preferred channel, and inbound receipt of A.h's reply Z
which the adapter hands back to the :class:`EscalationBroker`.

Adapter selection is config-driven (``AMMP_ESCALATION_ADAPTER``):

* ``log`` — write deliveries to the audit log. No outbound, no
  inbound. Useful for tests and for a first-boot install before
  the operator has wired Telegram / OpenClaw.
* ``telegram`` — outbound via a dedicated Telegram bot to A.h's
  configured chat id; inbound via long-polled getUpdates,
  matching replies to escalations via Telegram's
  ``reply_to_message`` threading.
"""

from __future__ import annotations

from ._base import DeliveryAdapter, DeliveryError
from ._log import LogDeliveryAdapter
from ._telegram import TelegramDeliveryAdapter

__all__ = [
    "DeliveryAdapter",
    "DeliveryError",
    "LogDeliveryAdapter",
    "TelegramDeliveryAdapter",
]
