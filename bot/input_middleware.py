"""Входящие события: присутствие, serial per-player и короткий дедуп.

Не импортирует main и не читает .env. Задержанные клики не превращаются
в скрытую очередь автоатак: конкурентное событие получает быстрый отказ.
"""
import time
from collections import OrderedDict


class InputMiddleware:
    def __init__(self, presence, on_input, blocked=lambda uid: False,
                 clock=time.monotonic):
        self.presence = presence
        self.on_input = on_input
        self.blocked = blocked
        self.clock = clock
        self.busy = set()
        self.seen = OrderedDict()

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user is None or getattr(user, "is_bot", False):
            return await handler(event, data)
        uid = user.id  # адаптер MAX передаст сюда внутренний player_id
        callback = hasattr(event, "data") and hasattr(event, "id")
        key = ("cb", uid, event.id) if callback else (
            "msg", uid, getattr(getattr(event, "chat", None), "id", uid),
            getattr(event, "message_id", None))
        now = self.clock()
        while self.seen and next(iter(self.seen.values())) <= now:
            self.seen.popitem(last=False)
        if key in self.seen:
            if callback:
                await event.answer()
            return None
        if not self.blocked(uid):
            self.presence.touch(uid)
        if uid in self.busy:
            await event.answer("⌛ Предыдущее действие ещё выполняется. Повторите нажатие.")
            return None
        self.busy.add(uid)
        self.seen[key] = now + 300.0
        # Ограничение памяти, включая события людей без созданного персонажа.
        while len(self.seen) > 10000:
            self.seen.popitem(last=False)
        try:
            if not self.blocked(uid):
                await self.on_input(uid)
            return await handler(event, data)
        finally:
            self.busy.discard(uid)
