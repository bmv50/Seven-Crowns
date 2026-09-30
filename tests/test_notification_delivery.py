"""Фактическая доставка: согласие, последние слоты квоты, конкуренция и TTL."""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from engine import notify
from engine.character import Character
from engine.notification_delivery import NotificationDelivery


def ts(hour, day=1):
    return datetime(2026, 9, day, hour, tzinfo=timezone.utc).timestamp()


def char(uid=1, consent=True, offset=3):
    ch = Character(uid=uid, name="Тест", cls="mage", race="human")
    notify.set_opt_in(ch, consent)
    notify.set_tz_offset(ch, offset)
    return ch


async def main():
    notify.ENABLED = True
    notify.clear()
    now = [ts(12)]
    ch = char()
    changed = []
    send = AsyncMock(return_value=True)
    delivery = NotificationDelivery({1: ch}.get, send, changed.append, lambda: now[0])
    assert await delivery.deliver(1, "daily_reset", "первый") == "sent"
    assert await delivery.deliver(1, "world_event", "последний") == "sent"
    assert await delivery.deliver(1, "world_boss", "лишний") == "drop"
    assert send.await_count == 2 and notify.quota_left(ch, now[0]) == 0
    assert changed == [1, 1]
    assert await delivery.deliver(1, "auction_sold", "сделка") == "sent"
    assert notify.quota_left(ch, now[0]) == 0
    assert await delivery.deliver(999, "auction_sold", "нет настроек") == "drop"

    # Очередь не расходует квоту: оба доступных слота действительно доставятся.
    ch = char()
    delivery.get_char = {1: ch}.get
    notify.emit(1, "daily_reset", "a")
    notify.emit(1, "world_event", "b")
    notify.emit(1, "world_event", "c")
    ready = notify.due(now[0], {1: ch})
    assert len(ready) == 3 and notify.quota_left(ch, now[0]) == 2
    results = [await delivery.deliver(r["uid"], r["category"], r["text"]) for r in ready]
    assert results == ["sent", "sent", "drop"]

    # Три разных производителя одновременно не выходят за суточную квоту.
    ch = char()
    delivery.get_char = {1: ch}.get
    async def slow_send(*args):
        await asyncio.sleep(0)
        return True
    delivery.send = slow_send
    results = await asyncio.gather(*[
        delivery.deliver(1, "world_event", str(i)) for i in range(8)])
    assert results.count("sent") == 2 and results.count("drop") == 6
    assert notify.quota_left(ch, now[0]) == 0

    ch = char()
    delivery.get_char = {1: ch}.get
    delivery.send = AsyncMock(return_value=False)
    assert await delivery.deliver(1, "daily_reset", "ошибка") == "failed"
    assert notify.quota_left(ch, now[0]) == 2
    delivery.send = AsyncMock(return_value=True)
    notify.set_opt_in(ch, False)
    assert await delivery.deliver(1, "auction_sold", "нет согласия") == "drop"
    notify.set_opt_in(ch, True)
    notify.set_pref(ch, "world_event", False)
    assert await delivery.deliver(1, "world_event", "категория off") == "drop"
    notify.set_pref(ch, "world_event", True)

    # Ночь + TTL короче ожидания: не доставляем уже закончившееся событие утром.
    now[0] = ts(21)  # полночь МСК
    assert await delivery.deliver(1, "world_event", "истечёт", ttl=40) == "drop"
    assert not notify.pending()
    assert await delivery.deliver(1, "daily_reset", "утром") == "defer"
    morning = notify.next_morning(now[0], ch)
    assert morning == ts(6, day=2) and notify._QUEUE[0]["fire_at"] == morning
    assert not notify.due(morning - 1, {1: ch})
    now[0] = morning
    r = notify.due(morning, {1: ch})[0]
    assert await delivery.deliver(1, r["category"], r["text"]) == "sent"
    assert notify.quota_left(ch, morning) == 1
    assert await delivery.deliver(1, "world_event", "expired", expires_at=morning - 1) == "drop"
    assert await delivery.deliver(1, "world_event", "TTL", expires_at=morning + 15) == "sent"
    assert delivery.send.call_args.args[3] == 15

    # По местной дате: на UTC-полуночи квота +12 ещё не сбрасывается.
    east = char(offset=12)
    notify.record_sent(east, "daily_reset", ts(23))
    assert notify.quota_left(east, ts(0, day=2)) == 1
    assert notify.quota_left(east, ts(12, day=2)) == 2
    west = char(offset=-2)
    assert notify.next_morning(ts(2), west) == ts(11)
    legacy = Character(uid=9, name="Старый", cls="warrior", race="human")
    assert not notify.opted_in(legacy)
    assert notify.allow(legacy, "world_event", ts(12)) == "drop"
    notify.ENABLED = False
    assert await delivery.deliver(1, "auction_sold", "глобально off") == "drop"
    notify.clear()
    print("OK: доставка, согласие, квота после успеха, конкуренция, местное время и TTL")


if __name__ == "__main__":
    asyncio.run(main())
