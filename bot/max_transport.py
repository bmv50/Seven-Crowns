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
from urllib.parse import urlsplit
from dataclasses import dataclass

from aiohttp import ClientSession, ClientTimeout, TCPConnector, FormData, web
from bot.max_tls import max_ssl_context
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
    if kind == 'message_callback':
        from engine.max_onboarding import valid_callback
        from engine.max_map import valid_callback as valid_map_callback
        from engine.max_items import valid_callback as valid_item_callback
        callback, message = data.get('callback'), data.get('message')
        if not isinstance(callback, dict) or not isinstance(message, dict):
            return None
        user, recipient = callback.get('user'), message.get('recipient')
        if not isinstance(user, dict) or not isinstance(recipient, dict):
            return None
        user_id, callback_id, payload = user.get('user_id'), callback.get('callback_id'), callback.get('payload')
        if (recipient.get('chat_type') != 'dialog' or user.get('is_bot')
                or type(user_id) is not int or user_id <= 0
                or not isinstance(callback_id, str) or not 1 <= len(callback_id) <= 256
                or not (valid_callback(payload) or valid_map_callback(payload) or valid_item_callback(payload))):
            return None
        return MaxInput(str(user_id), f'callback:{user_id}:{callback_id}', payload)
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
        self._image_tokens: dict[str, dict] = {}
        self._upload_lock = asyncio.Lock()

    async def start(self):
        connector = TCPConnector(ssl=max_ssl_context())
        self._session = ClientSession(timeout=ClientTimeout(total=10), connector=connector)

    async def close(self):
        if self._session:
            await self._session.close()
            self._session = None

    async def send_user(self, external_user_id: str, value: str):
        for part in text_parts(value):
            await self.send_chunk(external_user_id, part)

    async def answer_callback(self, callback_id):
        if self._session is None:
            raise RuntimeError('MAX client not started')
        async with self._session.post(f'{API_URL}/answers', params={'callback_id': callback_id},
                                      json={'notification': '✓'}, headers={'Authorization': self._token},
                                      allow_redirects=False) as response:
            if not 200 <= response.status < 300:
                raise MaxSendError(response.status)

    async def image_payload(self, asset):
        """Upload bundled art once per process; never forward auth to a CDN."""
        from engine.max_media import asset_path
        path = asset_path(asset)
        async with self._upload_lock:
            if asset in self._image_tokens:
                return self._image_tokens[asset]
            if asset.startswith('map:'):
                from engine.max_map import render_asset
                try:
                    path = await asyncio.to_thread(render_asset, asset)
                except (OSError, RuntimeError, ImportError):
                    _log.warning('max_map_render_failed')
                    return None  # Text and navigation remain deliverable.
            if asset.startswith('item:'):
                from engine.item_art import render_asset
                try:
                    path = await asyncio.to_thread(render_asset, asset)
                except (OSError, RuntimeError, ImportError):
                    _log.warning('max_item_render_failed')
                    return None
            async with self._session.post(f'{API_URL}/uploads', params={'type': 'image'},
                                          headers={'Authorization': self._token},
                                          allow_redirects=False) as response:
                if not 200 <= response.status < 300:
                    raise MaxSendError(response.status)
                endpoint = await response.json()
            url = endpoint.get('url') if isinstance(endpoint, dict) else None
            parsed = urlsplit(url) if isinstance(url, str) else None
            host = parsed.hostname if parsed else None
            if (not host or parsed.scheme != 'https' or parsed.username or parsed.password
                    or parsed.port not in (None, 443)
                    or not any(host == d or host.endswith('.'+d)
                               for d in ('max.ru', 'mycdn.me', 'okcdn.ru', 'oneme.ru'))):
                raise MaxSendError(502)
            if not path.is_file() or path.stat().st_size > 10 * 1024 * 1024:
                raise ValueError('Bundled MAX image missing or too large')
            with path.open('rb') as image:
                form = FormData()
                form.add_field('data', image, filename=path.name, content_type='image/jpeg')
                async with self._session.post(url, data=form, allow_redirects=False) as response:
                    if not 200 <= response.status < 300:
                        raise MaxSendError(response.status)
                    data = await response.json()
            photos = data.get('photos') if isinstance(data, dict) else None
            if (not isinstance(photos, dict) or not photos
                    or not all(isinstance(p, dict) and isinstance(p.get('token'), str) and p['token']
                               for p in photos.values())):
                raise MaxSendError(502)
            payload = {'photos': photos}
            if len(self._image_tokens) >= 512:
                self._image_tokens.pop(next(iter(self._image_tokens)))
            self._image_tokens[asset] = payload
            return payload

    async def send_chunk(self, external_user_id: str, text: str, *, keyboard=None, image_asset=None):
        """Send one persisted chunk, with no hidden network retry."""
        if self._session is None:
            raise RuntimeError("MAX client not started")
        if not text or len(text) > 3500:
            raise ValueError('MAX chunk must contain 1–3500 characters')
        body = {'text': text}
        attachments = []
        if image_asset is not None:
            image = await self.image_payload(image_asset)
            if image is not None:
                attachments.append({'type': 'image', 'payload': image})
        if keyboard is not None:
            from engine.max_navigation import validate
            attachments.append({'type': 'inline_keyboard', 'payload': {'buttons': validate(keyboard)}})
        if attachments:
            body['attachments'] = attachments
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
                    allow_redirects=False,
                ) as response:
                    if not 200 <= response.status < 300:
                        if response.status == 400 and image_asset is not None:
                            try:
                                error = await response.json()
                            except ValueError:
                                error = None
                            if isinstance(error, dict) and error.get('code') == 'attachment.not.ready':
                                raise MaxSendError(503, 2)
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
