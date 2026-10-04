"""Small, text-only MAX webhook transport for the shared game process.

The transport deliberately knows nothing about Telegram UI or game rules.
Only direct dialogs are accepted; the application supplies identity, dedup and
command handling. MAX credentials never appear in URLs or logs.
"""

import asyncio
import hmac
import logging
import math
import re
from dataclasses import dataclass

from aiohttp import ClientSession, ClientTimeout, web
from engine.max_outbox import MaxSendError, text_parts


API_URL = "https://platform-api2.max.ru"
_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MaxInput:
    external_user_id: str
    event_key: str
    text: str


def parse_update(data: dict) -> MaxInput | None:
    """Return supported private-dialog input, ignoring group and bot events."""
    if not isinstance(data, dict):
        return None
    kind = data.get("update_type")
    if kind == "bot_started":
        user = data.get("user") or {}
        if not isinstance(user, dict):
            return None
        user_id = user.get("user_id")
        stamp = data.get("timestamp")
        if type(user_id) is not int or user_id <= 0 or type(stamp) is not int:
            return None
        return MaxInput(str(user_id), f"start:{user_id}:{stamp}", "/start")
    if kind != "message_created":
        return None
    message = data.get("message") or {}
    if not isinstance(message, dict):
        return None
    sender = message.get("sender") or {}
    recipient = message.get("recipient") or {}
    body = message.get("body") or {}
    if not all(isinstance(part, dict) for part in (sender, recipient, body)):
        return None
    user_id = sender.get("user_id")
    mid = body.get("mid")
    value = body.get("text")
    if recipient.get("chat_type") != "dialog" or sender.get("is_bot"):
        return None
    if type(user_id) is not int or user_id <= 0 or not isinstance(mid, str) or not mid or len(mid) > 256:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return MaxInput(str(user_id), f"message:{user_id}:{mid}", value.strip()[:2000])


class MaxClient:
    def __init__(self, token: str):
        if not token:
            raise ValueError("MAX token is required")
        self._token = token
        self._session: ClientSession | None = None
        self._dialog_locks: dict[str, asyncio.Lock] = {}
        self._last_sent: dict[str, float] = {}

    async def start(self):
        self._session = ClientSession(timeout=ClientTimeout(total=10))

    async def close(self):
        if self._session:
            await self._session.close()
            self._session = None

    async def send_user(self, external_user_id: str, value: str):
        for part in text_parts(value):
            await self.send_chunk(external_user_id, part)

    async def send_chunk(self, external_user_id: str, text: str, *, keyboard=None):
        """Send one persisted chunk, with no hidden network retry."""
        if self._session is None:
            raise RuntimeError("MAX client not started")
        if not text or len(text) > 3500:
            raise ValueError('MAX chunk must contain 1–3500 characters')
        body = {'text': text}
        if keyboard is not None:
            from engine.max_navigation import validate
            body['attachments'] = [{'type': 'inline_keyboard', 'payload': {'buttons': validate(keyboard)}}]
        lock = self._dialog_locks.setdefault(external_user_id, asyncio.Lock())
        async with lock:
            loop = asyncio.get_running_loop()
            delay = self._last_sent.get(external_user_id, 0) + 0.55 - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                async with self._session.post(
                    f"{API_URL}/messages",
                    params={"user_id": external_user_id},
                    json=body,
                    headers={"Authorization": self._token},
                ) as response:
                    if not 200 <= response.status < 300:
                        try:
                            retry_after = float(response.headers.get('Retry-After', '0'))
                        except (TypeError, ValueError):
                            retry_after = 0
                        if not math.isfinite(retry_after):
                            retry_after = 0
                        raise MaxSendError(response.status, min(3600, max(0, retry_after)))
                    # A 2xx without the documented message object is not an ACK.
                    try:
                        payload = await response.json()
                    except ValueError:
                        raise MaxSendError(502) from None
                    message = payload.get('message') if isinstance(payload, dict) else None
                    body = message.get('body') if isinstance(message, dict) else None
                    mid = body.get('mid') if isinstance(body, dict) else None
                    if not isinstance(mid, str) or not mid:
                        raise MaxSendError(502)
            finally:
                # Throttle failures too, not only successful responses.
                self._last_sent[external_user_id] = loop.time()


class MaxWebhook:
    def __init__(self, secret: str, handle):
        if not re.fullmatch(r"[A-Za-z0-9_-]{5,256}", secret):
            raise ValueError("MAX webhook secret must match the API's 5–256 character format")
        self.secret = secret
        self.handle = handle

    async def receive(self, request: web.Request) -> web.Response:
        provided = request.headers.get("X-Max-Bot-Api-Secret", "")
        if not hmac.compare_digest(provided, self.secret):
            raise web.HTTPUnauthorized()
        try:
            data = await request.json()
        except (ValueError, UnicodeDecodeError):
            raise web.HTTPBadRequest()
        event = parse_update(data)
        if event is None:
            return web.json_response({"ok": True})
        try:
            await self.handle(event)
        except Exception:
            _log.exception("MAX update processing failed")
            raise web.HTTPServiceUnavailable()
        return web.json_response({"ok": True})

    def app(self) -> web.Application:
        app = web.Application(client_max_size=64 * 1024)
        app.router.add_post("/max/webhook", self.receive)
        return app
