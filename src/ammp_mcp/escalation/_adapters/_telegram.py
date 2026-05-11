"""Telegram delivery adapter — outbound via Bot API send_message, inbound via long-poll.

Wire contract:

* Outbound: ``POST /bot<token>/sendMessage`` with ``chat_id`` =
  ``settings.escalation_telegram_chat_id``. The message body carries
  the mentor → mentee context plus the question X. The message_id
  returned by Telegram is recorded as ``delivery_ref`` on the
  escalation and is also the reply-threading anchor for inbound
  matching.
* Inbound: a background task long-polls ``GET /bot<token>/getUpdates``
  with the offset cursor advanced after each batch. Any update whose
  ``message.reply_to_message.message_id`` matches a pending
  escalation's ``delivery_ref`` is treated as A.h's answer Z: the
  store is updated, the broker resolves the waiter, and the
  long-running ``EscalateToHumanMentor`` tool call returns Z to B.a.

Why long-poll rather than webhook: long-poll is one outbound https
connection per ~25 s window; webhook needs an inbound https
endpoint on this server that Telegram can reach. Long-poll wins on
firewall simplicity for a single-instance home deployment behind a
Cloudflare Tunnel.

The adapter is **not active until** ``settings.escalation_telegram_bot_token``
and ``settings.escalation_telegram_chat_id`` are both set. When unset,
``ammp serve`` falls through to :class:`LogDeliveryAdapter`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

import httpx

from .._models import Escalation
from .._service import EscalationBroker, EscalationStore
from ._base import DeliveryAdapter, DeliveryError

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org"


class TelegramDeliveryAdapter(DeliveryAdapter):
    """Telegram-bot-backed delivery adapter.

    The inbound long-poll task starts in :meth:`start`.

    Args:
        bot_token: Bot token from @BotFather, e.g.
            ``"123456:AAH…"``.
        chat_id: The A.h chat id to deliver escalations to.
            Numeric string (e.g. ``"123456789"`` for a private chat).
        http_timeout: Per-request timeout for Bot API calls.
            Long-poll uses ``http_timeout`` + the poll window.
        poll_seconds: getUpdates long-poll window. Higher values
            reduce request rate; capped at 50 s by Telegram.
    """

    kind = "telegram"

    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        http_timeout: float = 15.0,
        poll_seconds: int = 25,
    ) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._http_timeout = http_timeout
        self._poll_seconds = poll_seconds
        self._client: httpx.AsyncClient | None = None
        self._listener_task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()
        self._broker: EscalationBroker | None = None
        self._store: EscalationStore | None = None
        self._update_offset = 0

    @property
    def _base_url(self) -> str:
        """Bot API base URL with token. Never logged or surfaced."""
        return f"{_TELEGRAM_API}/bot{self._bot_token}"

    async def start(self, broker: EscalationBroker, store: EscalationStore) -> None:
        """Open the HTTP client and spawn the long-poll listener."""
        self._broker = broker
        self._store = store
        self._client = httpx.AsyncClient(timeout=self._http_timeout)
        # Cold-start with offset 0 so we don't redeliver answers from a
        # previous server lifetime (Telegram stores updates for ~24h).
        # We could also call deleteWebhook to ensure no webhook is
        # registered, but the demo deployment never installs one.
        self._listener_task = asyncio.create_task(self._poll_forever(), name="telegram-escalation-listener")
        logger.info("escalation delivery adapter: telegram (chat_id=%s) started", self._chat_id)

    async def deliver(self, escalation: Escalation) -> str | None:
        """Send X to A.h via Bot API ``sendMessage``.

        Args:
            escalation: The pending escalation whose question text to forward.

        Returns:
            Telegram message_id (as string) for inbound reply threading.

        Raises:
            DeliveryError: When the Bot API call fails or returns
                ``ok=false``.
        """
        if self._client is None:
            raise DeliveryError("telegram adapter not started")
        body = self._format_outbound(escalation)
        try:
            r = await self._client.post(
                f"{self._base_url}/sendMessage",
                json={"chat_id": self._chat_id, "text": body, "parse_mode": "Markdown"},
            )
            data: dict[str, Any] = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise DeliveryError(f"telegram sendMessage failed: {e}") from e
        if not data.get("ok"):
            raise DeliveryError(f"telegram sendMessage rejected: {data.get('description')!r}")
        message_id = data["result"]["message_id"]
        return str(message_id)

    async def stop(self) -> None:
        """Stop the inbound listener and close the HTTP client."""
        self._stopped.set()
        if self._listener_task is not None:
            self._listener_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._listener_task
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def _format_outbound(escalation: Escalation) -> str:
        """Render the outbound message to A.h.

        Markdown, structured so the human can scan it quickly:
        mentor + mentee compartmentalised, question fenced, reply
        instruction at the bottom (reply to this message in Telegram).
        """
        ctx = f"\n\n*Context:* {escalation.context}" if escalation.context else ""
        return (
            f"*Escalation* `{escalation.id[:8]}`\n"
            f"From mentee *{escalation.mentee_slug}* "
            f"via mentor *{escalation.mentor_slug}*.\n\n"
            f"```\n{escalation.question}\n```"
            f"{ctx}\n\n"
            "_Reply to this message with your answer; it'll be relayed to the mentee._"
        )

    async def _poll_forever(self) -> None:
        """Long-poll Telegram getUpdates, dispatch reply matches.

        Runs until :meth:`stop` is called (sets ``self._stopped``) or
        the task is cancelled. Each batch advances ``_update_offset``;
        any update whose ``message.reply_to_message`` matches a
        pending escalation's ``delivery_ref`` triggers
        :meth:`EscalationBroker.resolve`.
        """
        assert self._client is not None
        assert self._broker is not None
        assert self._store is not None
        while not self._stopped.is_set():
            try:
                r = await self._client.get(
                    f"{self._base_url}/getUpdates",
                    params={"timeout": self._poll_seconds, "offset": self._update_offset},
                    timeout=self._poll_seconds + 5,
                )
                data: dict[str, Any] = r.json()
            except Exception as e:
                logger.warning("telegram getUpdates failed (will retry): %s", e)
                await asyncio.sleep(2.0)
                continue
            if not data.get("ok"):
                logger.warning("telegram getUpdates rejected: %s", data)
                await asyncio.sleep(2.0)
                continue
            for update in data.get("result", []):
                self._update_offset = max(self._update_offset, int(update["update_id"]) + 1)
                self._handle_update(update)

    def _handle_update(self, update: dict[str, Any]) -> None:
        """Match one update to a pending escalation, resolve the waiter.

        Args:
            update: One element of getUpdates' ``result`` array.
        """
        assert self._broker is not None
        assert self._store is not None
        message = update.get("message")
        if not isinstance(message, dict):
            return
        reply_to = message.get("reply_to_message")
        if not isinstance(reply_to, dict):
            return
        parent_id = str(reply_to.get("message_id", ""))
        if not parent_id:
            return
        # Find the pending escalation whose delivery_ref matches.
        match = None
        for esc in self._store.list_by_status("delivered"):
            if esc.delivery_ref == parent_id:
                match = esc
                break
        if match is None:
            # Late reply: A.h answered after the MCP call was cancelled
            # or expired. The mentee can't receive the answer anymore
            # (no waiter), but log it explicitly so the operator can
            # see the question got an answer they may want to forward
            # manually. Silent drops here masked a real bug for a full
            # session before we noticed (2026-05-11).
            for esc in (*self._store.list_by_status("cancelled"), *self._store.list_by_status("expired")):
                if esc.delivery_ref == parent_id:
                    logger.warning(
                        "telegram: late reply to escalation %s (status=%s) — call already gone, answer dropped",
                        esc.id,
                        esc.status,
                    )
                    return
            return
        text = (message.get("text") or "").strip()
        if not text:
            logger.warning("telegram reply to escalation %s carried no text", match.id)
            return
        # Persist the answer then wake the waiter — order matters: the
        # waiter triggers the long-running tool call to return, and we
        # want the store to be consistent before that runs.
        from datetime import UTC, datetime  # local import keeps the module surface tight

        self._store.update(match.id, status="answered", answered_at=datetime.now(UTC), answer=text)
        self._broker.resolve(match.id, text)
        logger.info("telegram: resolved escalation %s with %d-char reply", match.id, len(text))
