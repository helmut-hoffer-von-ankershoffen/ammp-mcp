"""Log-only delivery adapter — no outbound, no inbound.

Useful as a default when no production adapter is configured. Writes
the outbound delivery to the python logger at INFO level so an
operator tailing ``ammp-mcp.out.log`` sees each escalation as it
flows through. Inbound is a no-op — escalations stay ``pending`` /
``delivered`` until the long-running tool call is cancelled or the
server restarts (the boot path marks them ``expired``).

For tests that need to *simulate* an inbound reply, call
:meth:`LogDeliveryAdapter.simulate_inbound(broker, id, answer)`
directly — this is the test seam, not a production primitive.
"""

from __future__ import annotations

import logging

from .._models import Escalation
from .._service import EscalationBroker, EscalationStore
from ._base import DeliveryAdapter

logger = logging.getLogger(__name__)


class LogDeliveryAdapter(DeliveryAdapter):
    """No-op adapter — outbound logs, inbound never fires.

    Production should swap this out for :class:`TelegramDeliveryAdapter`
    or an OpenClaw bridge once configured.
    """

    kind = "log"

    async def start(self, broker: EscalationBroker, store: EscalationStore) -> None:  # noqa: ARG002 — adapter contract
        """No-op start — log-only adapter has no inbound listener."""
        logger.info(
            "escalation delivery adapter: log (no inbound). "
            "Pending escalations will only resolve via explicit operator action."
        )

    async def deliver(self, escalation: Escalation) -> str | None:
        """Log the outbound — no actual channel write."""
        logger.info(
            "escalation %s: would deliver to A.h (mentor=%s mentee=%s, %d chars)",
            escalation.id,
            escalation.mentor_slug,
            escalation.mentee_slug,
            len(escalation.question),
        )
        return None

    async def stop(self) -> None:
        """No-op stop — nothing to tear down."""
