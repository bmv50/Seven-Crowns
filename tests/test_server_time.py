"""Темп действий, серверные таймеры, присутствие и повторы входящих событий."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from engine.character import Character
from engine.interaction import Presence, ActionPacer, PlayerClock
from bot.input_middleware import InputMiddleware


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_pacing():
    clock = Clock()
    pacer = ActionPacer(clock=clock)
    assert pacer.acquire(1) == 0
    for _ in range(100):
        assert pacer.acquire(1) == 1  # атака/умение/зелье/побег — один uid
    assert pacer.acquire(2) == 0
    clock.now = 0.5
    assert pacer.acquire(1) == 0.5
    clock.now = 1
    assert pacer.acquire(1) == 0
    clock.now = 1000
    assert pacer.acquire(1) == 0
    assert pacer.acquire(1) == 1  # пауза не копит запас атак
    pacer.refund(1)
    assert pacer.acquire(1) == 0
    # Воспроизводим 20 секунд: 200 кликов и 20 кликов дают одинаковые 20 ходов.
    counts = []
    for interval in (0.1, 1.0):
        clock.now = 0
        p = ActionPacer(clock=clock)
        accepted = 0
        for i in range(round(20 / interval)):
            clock.now = i * interval
            accepted += p.acquire(7) == 0
        counts.append(accepted)
    assert counts == [20, 20], counts


def test_player_clock():
    now = Clock()
    changed = []
    timer = PlayerClock(clock=now, changed=changed.append)
    ch = Character(uid=1, name="Маг", cls="mage", race="human")
    ch.init_vitals()
    ch.mp = 0
    ch.cooldowns = {"fireball": 5}
    ch.effects = [{"type": "shield", "amount": 20, "turns": 4}]
    assert not timer.advance(ch)
    now.now = 0.9
    for _ in range(100):
        assert not timer.advance(ch)
    assert ch.mp == 0 and ch.cooldowns["fireball"] == 5
    now.now = 2.4
    timer.advance(ch)
    assert ch.mp > 0 and ch.cooldowns["fireball"] == 3 and ch.effects[0]["turns"] == 2
    before = ch.mp
    now.now = 3.0
    timer.advance(ch, regenerate=False)
    assert ch.cooldowns["fireball"] == 2 and ch.mp == before
    now.now = 1000000
    timer.advance(ch)
    assert ch.mp == ch.max_resource and ch.cooldowns["fireball"] == 0 and not ch.effects
    assert changed == [1, 1, 1]
    timer.forget(1)
    ch.mp = 0
    assert not timer.advance(ch) and ch.mp == 0  # новый герой не наследует таймер
    rage = Character(uid=2, name="Воин", cls="warrior", race="human")
    rage.mp = 0
    timer.advance(rage)
    now.now += 10
    timer.advance(rage)
    assert rage.mp == 0  # ярость не регенерирует от времени


def test_presence():
    now = Clock()
    p = Presence(clock=now)
    assert not p.active(1)
    p.touch(1)
    assert p.active(1)  # отметка 0 тоже валидна
    now.now = 299.9
    assert p.active(1)
    now.now = 300
    assert not p.active(1)
    p.touch(1)
    assert p.active(1)
    p.forget(1)
    assert not p.active(1)


def event(uid=1, number=1, callback=False):
    e = SimpleNamespace(from_user=SimpleNamespace(id=uid, is_bot=False), answer=AsyncMock())
    if callback:
        e.id = str(number)
        e.data = "atk:rat"
    else:
        e.chat = SimpleNamespace(id=uid)
        e.message_id = number
    return e


async def test_input():
    now = Clock()
    presence = Presence(clock=now)
    on_input = AsyncMock()
    middleware = InputMiddleware(presence, on_input, clock=now, blocked=lambda uid: uid == 9)
    handler = AsyncMock(return_value="ok")
    e = event(callback=True)
    assert await middleware(handler, e, {}) == "ok"
    assert presence.active(1) and on_input.await_count == 1
    assert await middleware(handler, e, {}) is None
    assert handler.await_count == 1 and on_input.await_count == 1
    assert e.answer.await_count == 1
    await middleware(handler, event(uid=2, callback=True), {})
    assert handler.await_count == 2  # одинаковый внешний id разных игроков
    now.now = 301
    await middleware(handler, e, {})
    assert handler.await_count == 3
    await middleware(handler, event(9), {})
    assert not presence.active(9) and on_input.await_count == 3

    # Не накапливаем очередь действий за долгой отправкой/LLM.
    entered = asyncio.Event()
    release = asyncio.Event()
    async def slow_handler(e, data):
        entered.set()
        await release.wait()
    first = asyncio.create_task(middleware(slow_handler, event(number=2), {}))
    await entered.wait()
    concurrent = event(number=3)
    assert await middleware(handler, concurrent, {}) is None
    assert concurrent.answer.await_count == 1 and handler.await_count == 4
    release.set()
    await first
    assert not middleware.busy
    async def broken(e, data):
        raise RuntimeError("synthetic")
    try:
        await middleware(broken, event(number=4), {})
    except RuntimeError:
        pass
    assert not middleware.busy


async def main():
    test_pacing()
    test_player_clock()
    test_presence()
    await test_input()
    # Реальные формулы боя, все классы: спам не улучшает HP/MP/победы/время.
    from sims.sim_server_combat import measure
    result = measure(runs=2, levels=(1, 6, 15, 25))
    assert len(result) == 24 and all(not r["spam_advantage"] for r in result)
    print("OK: общий темп, серверные таймеры, присутствие, дедуп и конкурентный ввод")


if __name__ == "__main__":
    asyncio.run(main())
