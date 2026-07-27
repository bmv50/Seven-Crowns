# -*- coding: utf-8 -*-
"""Тесты динамических мировых событий. Запуск: python test_events.py"""
from engine import events
from engine.world import World


class FakeRng:
    """Детерминированный rng: random()->val, choice->первый, shuffle->no-op."""
    def __init__(self, val=0.0): self.val = val
    def random(self): return self.val
    def choice(self, seq): return seq[0]
    def shuffle(self, seq): pass


def test_disabled_noop():
    events.ENABLED = False; events.reset()
    w = World()
    assert events.maybe_start(w, now=1000) == []
    assert events.modifiers("любая") == {"xp": 1.0, "gold": 1.0, "loot": 1.0}
    print("✓ выключено = no-op")


def test_start_and_modifiers():
    events.ENABLED = True; events.reset()
    w = World()
    # форсируем запуск (random()=0 < START_CHANCE), choice -> первый ивент
    msgs = events.maybe_start(w, now=1000, rng=FakeRng(0.0))
    assert msgs and len(events.active()) == 1
    d = events.active()[0]["def"]
    zone = d.get("zone")
    mod = events.modifiers(zone)
    assert mod["xp"] > 1.0 or mod["gold"] > 1.0 or mod["loot"] > 1.0
    print("✓ событие стартует и даёт множители:", {k: v for k, v in mod.items() if v != 1.0})


def test_invasion_spawns_and_despawn():
    events.ENABLED = True; events.reset()
    w = World()
    # найти invasion-ивент и запустить именно его
    inv = [k for k, v in events._DEFS.items() if v.get("type") == "invasion"]
    assert inv, "нет invasion в events.yaml"
    eid = inv[0]; d = events._DEFS[eid]
    before = sum(len(v) for v in w.mobs.values())
    events._start(w, eid, d, now=1000, rng=FakeRng(0.0))
    after = sum(len(v) for v in w.mobs.values())
    assert after > before, "вторжение не заспавнило мобов"
    # истечение → деспавн
    events.active()[0]["ends_at"] = 0
    events.tick(w, now=9999)
    end = sum(len(v) for v in w.mobs.values())
    assert end == before and not events.active()
    print("✓ вторжение спавнит и деспавнит мобов")


def test_max_active_and_cooldown():
    events.ENABLED = True; events.reset()
    w = World()
    events.maybe_start(w, now=1000, rng=FakeRng(0.0))
    # сразу второй раз — не стартует (cooldown CHECK_EVERY и MAX_ACTIVE)
    assert events.maybe_start(w, now=1001, rng=FakeRng(0.0)) == []
    print("✓ кулдаун и лимит активных соблюдаются")


def test_announcements_have_ttl():
    """Анонс события живёт столько же, сколько само событие.

    Плейтест владельца: «Нашествие!» висело в чате сутками после конца
    нашествия. Движок отдаёт транспорту пары (текст, сколько жить), а бот
    вешает на них самоудаление (bot/main.broadcast_world_event).
    """
    events.ENABLED = True; events.reset()
    w = World()
    eid = next(iter(events._DEFS))
    dur = 900
    msgs, reason = events.start(eid, world=w, now=1000, duration=dur)
    assert msgs and reason is None, (msgs, reason)

    anns = events.drain_announcements()
    assert len(anns) == 1, anns
    text, ttl = anns[0]
    assert text == msgs[0]
    # duration клампится границами события — сверяем с фактическим ends_at
    assert ttl == max(events.MIN_TTL, events.active()[0]["ends_at"] - 1000), ttl
    print("✓ анонс старта получает время жизни, равное длительности события")

    # забрали насовсем: второй вызов пуст, иначе цикл разошлёт анонс дважды
    assert events.drain_announcements() == []
    print("✓ буфер анонсов забирается один раз")

    # завершение — тоже временное сообщение, но короткоживущее
    ended = events.tick(w, now=10**6)
    assert ended and not events.active()
    anns = events.drain_announcements()
    assert len(anns) == 1 and anns[0][1] == events.ENDED_TTL, anns
    assert "завершилось" in anns[0][0]
    print("✓ сообщение о завершении живёт ENDED_TTL и не остаётся навсегда")

    # reset чистит буфер — иначе анонсы протекают между сезонами/тестами
    events.start(eid, world=w, now=2000)
    events.reset()
    assert events.drain_announcements() == []
    print("✓ reset() очищает буфер анонсов")


if __name__ == "__main__":
    test_disabled_noop()
    test_start_and_modifiers()
    test_invasion_spawns_and_despawn()
    test_max_active_and_cooldown()
    test_announcements_have_ttl()
    events.ENABLED = False; events.reset()
    print("\n=== events OK ===")
