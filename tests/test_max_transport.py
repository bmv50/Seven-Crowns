"""MAX boundary: private updates, authentication and at-most-once claims."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiohttp import web

from bot.max_transport import MaxClient, MaxWebhook, parse_update
from engine.db import Database, SCHEMA


def _message(*, chat_type="dialog", mid="m-1", text="/start", sender=42):
    return {"update_type": "message_created", "message": {
        "sender": {"user_id": sender},
        "recipient": {"chat_type": chat_type, "chat_id": 7},
        "body": {"mid": mid, "text": text},
    }}


def test_parse():
    event = parse_update(_message())
    assert (event.external_user_id, event.event_key, event.text) == ("42", "message:42:m-1", "/start")
    assert parse_update(_message(chat_type="chat")) is None
    assert parse_update(_message(mid="")) is None
    assert parse_update(_message(text="   ")) is None
    assert parse_update({"update_type": "message_created", "message": {
        "sender": {"user_id": 42, "is_bot": True},
        "recipient": {"chat_type": "dialog"}, "body": {"mid": "x", "text": "x"}}}) is None
    started = parse_update({"update_type": "bot_started", "timestamp": 123,
                            "user": {"user_id": 42}})
    assert started.event_key == "start:42:123"
    assert parse_update({"update_type": "message_edited"}) is None


async def test_webhook():
    handled = AsyncMock()
    hook = MaxWebhook("secret123", handled)
    request = SimpleNamespace(headers={"X-Max-Bot-Api-Secret": "wrong"},
                              json=AsyncMock(return_value=_message()))
    try:
        await hook.receive(request)
    except web.HTTPUnauthorized:
        pass
    else:
        raise AssertionError("wrong secret accepted")
    handled.assert_not_awaited()
    request.headers["X-Max-Bot-Api-Secret"] = "secret123"
    response = await hook.receive(request)
    assert response.status == 200
    handled.assert_awaited_once()


class _Response:
    def __init__(self):
        self.checked = False
        self.status = 200
        self.headers = {}

    async def json(self):
        return {'message': {'body': {'mid': 'm-1'}}}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def raise_for_status(self):
        self.checked = True


async def test_outgoing():
    client = MaxClient("private-token")
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return _Response()
    client._session = SimpleNamespace(post=post)
    await client.send_user("42", "a" * 4100)
    assert len(calls) == 2
    assert calls[0][1]["headers"] == {"Authorization": "private-token"}
    assert calls[0][1]["params"] == {"user_id": "42"}
    assert all(c[1]['allow_redirects'] is False for c in calls)
    assert all(len(c[1]["json"]["text"]) <= 3500 for c in calls)


def test_schema():
    assert "CREATE TABLE IF NOT EXISTS max_inbox" in SCHEMA
    assert "event_key TEXT PRIMARY KEY" in SCHEMA


if __name__ == "__main__":
    test_parse()
    test_schema()
    asyncio.run(test_webhook())
    asyncio.run(test_outgoing())
    print("OK: MAX transport parsing, authentication, outbound and dedup schema")
