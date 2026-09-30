"""Единая проверка политики и учёт доставки, без сетевых зависимостей."""
import asyncio
import time

from . import notify


class NotificationDelivery:
    def __init__(self, get_char, send, changed=None, clock=time.time):
        self.get_char = get_char
        self.send = send
        self.changed = changed
        self.clock = clock
        self.locks = {}

    async def deliver(self, uid, category, text, ttl=None, expires_at=None):
        if not notify.ENABLED:
            return "drop"
        lock = self.locks.setdefault(uid, asyncio.Lock())
        async with lock:
            ch = self.get_char(uid)
            if ch is None:
                return "drop"  # отсутствие настроек не означает согласие
            now = self.clock()
            deadline = expires_at if expires_at is not None else (
                now + ttl if ttl is not None else None)
            if deadline is not None and deadline <= now:
                return "drop"
            verdict = notify.allow(ch, category, now)
            if verdict == "defer":
                morning = notify.next_morning(now, ch)
                if deadline is None or morning < deadline:
                    notify.emit(uid, category, text, fire_at=morning, expires_at=deadline)
                    return "defer"
                return "drop"
            if verdict != "send":
                return "drop"
            remaining = max(0.0, deadline - now) if deadline is not None else None
            if not await self.send(uid, category, text, remaining):
                return "failed"
            # Отправленная запись не проверяется повторно через allow():
            # последний разрешённый push не теряется на границе квоты.
            notify.record_sent(ch, category, self.clock())
            if self.changed:
                self.changed(uid)
            return "sent"
