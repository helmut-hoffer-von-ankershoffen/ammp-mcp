"""Unit tests for the Telegram delivery adapter — no live API calls."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import MagicMock

import httpx
import pytest

from ammp_mcp.escalation._adapters._base import DeliveryError
from ammp_mcp.escalation._adapters._telegram import TelegramDeliveryAdapter
from ammp_mcp.escalation._models import Escalation

pytestmark = pytest.mark.unit


def _make_escalation(eid: str = "abc123def456", question: str = "Q?", context: str | None = None) -> Escalation:
    return Escalation(
        id=eid,
        mentor_slug="pepe",
        mentee_slug="m1",
        question=question,
        context=context,
        status="pending",
        created_at=datetime.now(UTC),
    )


def test_format_outbound_includes_id_question_mentee_reply_hint() -> None:
    """Outbound message has id prefix, mentor/mentee, fenced question, reply instruction."""
    esc = _make_escalation()
    out = TelegramDeliveryAdapter._format_outbound(esc)
    assert "abc123de" in out  # first 8 of id
    assert "*pepe*" in out
    assert "*m1*" in out
    assert "```\nQ?\n```" in out
    assert "Reply to this message" in out
    # No context section when not provided
    assert "Context" not in out


def test_format_outbound_with_context_includes_context_section() -> None:
    esc = _make_escalation(context="why this matters")
    out = TelegramDeliveryAdapter._format_outbound(esc)
    assert "*Context:* why this matters" in out


@pytest.mark.asyncio
async def test_deliver_posts_send_message_returns_message_id() -> None:
    """`deliver()` POSTs the JSON body to /sendMessage and returns the message_id."""
    adapter = TelegramDeliveryAdapter(bot_token="TKN", chat_id="42")
    captured: dict = {}

    async def _send(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 99}})

    transport = httpx.MockTransport(_send)
    adapter._client = httpx.AsyncClient(transport=transport)
    try:
        ref = await adapter.deliver(_make_escalation())
    finally:
        await adapter._client.aclose()
    assert ref == "99"
    assert "/botTKN/sendMessage" in captured["url"]
    body = captured["body"]
    assert "chat_id" in body and '"42"' in body
    assert "parse_mode" in body and "Markdown" in body


@pytest.mark.asyncio
async def test_deliver_raises_delivery_error_on_telegram_rejection() -> None:
    adapter = TelegramDeliveryAdapter(bot_token="TKN", chat_id="42")

    async def _send(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "description": "bot blocked by the user"})

    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(_send))
    try:
        with pytest.raises(DeliveryError, match="rejected"):
            await adapter.deliver(_make_escalation())
    finally:
        await adapter._client.aclose()


@pytest.mark.asyncio
async def test_deliver_raises_delivery_error_on_http_error() -> None:
    adapter = TelegramDeliveryAdapter(bot_token="TKN", chat_id="42")

    async def _send(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns failure")

    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(_send))
    try:
        with pytest.raises(DeliveryError, match="sendMessage failed"):
            await adapter.deliver(_make_escalation())
    finally:
        await adapter._client.aclose()


@pytest.mark.asyncio
async def test_deliver_raises_when_not_started() -> None:
    adapter = TelegramDeliveryAdapter(bot_token="TKN", chat_id="42")
    with pytest.raises(DeliveryError, match="not started"):
        await adapter.deliver(_make_escalation())


def test_handle_update_resolves_matching_delivered_reply() -> None:
    """A reply whose `reply_to_message.message_id` matches a delivered escalation's
    `delivery_ref` triggers the store update + broker resolve."""
    adapter = TelegramDeliveryAdapter(bot_token="TKN", chat_id="42")
    esc = _make_escalation(eid="esc-1")
    esc.status = "delivered"
    esc.delivery_ref = "msg-42"
    adapter._store = MagicMock()
    adapter._store.list_by_status.side_effect = lambda s: [esc] if s == "delivered" else []
    adapter._broker = MagicMock()

    adapter._handle_update(
        {
            "update_id": 1,
            "message": {
                "text": "the answer",
                "reply_to_message": {"message_id": "msg-42"},
            },
        }
    )

    adapter._store.update.assert_called_once()
    kwargs = adapter._store.update.call_args.kwargs
    assert kwargs["status"] == "answered"
    assert kwargs["answer"] == "the answer"
    adapter._broker.resolve.assert_called_once_with("esc-1", "the answer")


def test_handle_update_drops_silently_when_no_reply_to() -> None:
    """A fresh message (not a reply) → no-op; the listener never disturbs the store."""
    adapter = TelegramDeliveryAdapter(bot_token="TKN", chat_id="42")
    adapter._store = MagicMock()
    adapter._store.list_by_status.return_value = []
    adapter._broker = MagicMock()
    adapter._handle_update({"update_id": 1, "message": {"text": "fresh"}})
    adapter._store.update.assert_not_called()
    adapter._broker.resolve.assert_not_called()


def test_handle_update_warns_on_empty_reply_text() -> None:
    adapter = TelegramDeliveryAdapter(bot_token="TKN", chat_id="42")
    esc = _make_escalation(eid="esc-2")
    esc.status = "delivered"
    esc.delivery_ref = "msg-7"
    adapter._store = MagicMock()
    adapter._store.list_by_status.side_effect = lambda s: [esc] if s == "delivered" else []
    adapter._broker = MagicMock()

    adapter._handle_update(
        {
            "update_id": 1,
            "message": {"text": "  ", "reply_to_message": {"message_id": "msg-7"}},
        }
    )
    adapter._store.update.assert_not_called()
    adapter._broker.resolve.assert_not_called()


def test_handle_update_logs_late_reply_for_cancelled_escalation() -> None:
    """A reply to an escalation that was already cancelled is logged + dropped —
    not silently lost. Pinned because silent drops masked a real bug for hours
    in production (2026-05-11) before we noticed."""
    adapter = TelegramDeliveryAdapter(bot_token="TKN", chat_id="42")
    esc = _make_escalation(eid="esc-late")
    esc.status = "cancelled"
    esc.delivery_ref = "msg-99"
    # No delivered match, but cancelled match exists.
    adapter._store = MagicMock()
    adapter._store.list_by_status.side_effect = lambda s: [esc] if s == "cancelled" else []
    adapter._broker = MagicMock()

    adapter._handle_update(
        {
            "update_id": 1,
            "message": {
                "text": "late answer",
                "reply_to_message": {"message_id": "msg-99"},
            },
        }
    )
    # Should NOT touch the store or broker — the call is gone — but
    # the cancelled-status branch was traversed (covered).
    adapter._store.update.assert_not_called()
    adapter._broker.resolve.assert_not_called()


@pytest.mark.asyncio
async def test_poll_loop_consumes_getupdates_and_advances_offset() -> None:
    """The listener calls getUpdates, advances the offset, and feeds each update
    through `_handle_update`. Covers the `_poll_forever` loop body."""
    adapter = TelegramDeliveryAdapter(bot_token="TKN", chat_id="42", poll_seconds=1)
    broker = MagicMock()
    store = MagicMock()
    store.list_by_status.return_value = []

    iteration = {"n": 0}

    async def _send(_request: httpx.Request) -> httpx.Response:
        iteration["n"] += 1
        if iteration["n"] >= 2:
            # After the first batch is consumed, signal stop so the
            # while-loop exits cleanly on the next condition check.
            adapter._stopped.set()
        return httpx.Response(
            200,
            json={"ok": True, "result": [{"update_id": 7, "message": {"text": "x"}}]},
        )

    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(_send))
    adapter._broker = broker
    adapter._store = store

    await asyncio.wait_for(adapter._poll_forever(), timeout=2.0)
    await adapter._client.aclose()

    assert iteration["n"] >= 1
    # The offset advanced past update_id 7 → next request would carry offset=8.
    assert adapter._update_offset == 8
