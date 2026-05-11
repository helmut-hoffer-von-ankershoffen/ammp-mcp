"""Delivery-adapter contract.

A :class:`DeliveryAdapter` is the seam between ammp-mcp's
escalation broker and the human mentor's communication channel.
The server owns the broker + store; the adapter owns the channel.

Lifecycle:

* ``start(broker, store)`` — adapter spins up its inbound listener
  (e.g. Telegram getUpdates loop, IMAP poller, OpenClaw webhook
  handler). The broker reference lets the adapter call
  :meth:`EscalationBroker.resolve` when replies arrive.
* ``deliver(escalation)`` — outbound. Send X to A.h. Return an
  adapter-specific reference (e.g. Telegram message_id) the
  inbound side will use to thread replies. Or raise
  :class:`DeliveryError` if the channel is unreachable.
* ``stop()`` — graceful shutdown of the inbound listener task.

Adapters MUST be safe to call from the FastMCP event loop;
threaded inbound work must hand off via
:meth:`asyncio.AbstractEventLoop.call_soon_threadsafe` (the broker
already does this internally).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .._models import Escalation
    from .._service import EscalationBroker, EscalationStore


class DeliveryError(RuntimeError):
    """Raised when an adapter cannot deliver an escalation outbound.

    The escalation is marked ``cancelled`` with this error's message
    as the cancel_reason, and the long-running tool call surfaces an
    in-band ``{"error": "delivery_failed"}`` to B.a so the mentee
    doesn't hang forever on an undeliverable channel.
    """


class DeliveryAdapter(ABC):
    """Outbound + inbound bridge to a human mentor's channel.

    Concrete adapters live next to this file:
    :class:`LogDeliveryAdapter` (no-op, dev default),
    :class:`TelegramDeliveryAdapter` (production).
    """

    #: Human-readable label for status / health surfaces.
    kind: str = "abstract"

    @abstractmethod
    async def start(self, broker: EscalationBroker, store: EscalationStore) -> None:
        """Boot the adapter — spin up the inbound listener.

        Args:
            broker: The in-process waiter registry to resolve when
                replies arrive.
            store: The persistence store, in case the adapter wants
                to surface adapter-specific state (e.g. last-known
                delivery_ref) on inbound matching.
        """

    @abstractmethod
    async def deliver(self, escalation: Escalation) -> str | None:
        """Forward this escalation's question X to A.h.

        Args:
            escalation: The pending escalation to deliver.

        Returns:
            An adapter-specific reference for inbound threading
            (e.g. Telegram message_id as a string), or ``None`` if
            the adapter doesn't track per-message references.

        Raises:
            DeliveryError: When the channel is unreachable or the
                outbound call failed unrecoverably.
        """

    @abstractmethod
    async def stop(self) -> None:
        """Tear down the inbound listener cleanly."""
