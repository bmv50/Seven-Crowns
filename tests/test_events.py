# -*- coding: utf-8 -*-
"""Тесты динамических мировых событий. Запуск: python test_events.py"""
import asyncio

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


def _goal_event_id():
    """id первого события с общей целью в контенте."""
    return next(eid for eid, d in events._DEFS.items() if d.get("goal"))


def test_goal_shared_counter():
    """Событие с целью: счётчик ОДИН на всех, вклад считается по игрокам."""
    events.ENABLED = True
    events.reset()
    eid = _goal_event_id()
    zone = "Пепельные Пустоши"
    events.start(eid, zone=zone, world=World(), now=1000.0)
    e = events.active()[0]
    need = int(e["def"]["goal"]["count"])
    assert e["progress"] == 0 and e["contrib"] == {}, "цель стартует с нуля"

    # первое же убийство не должно падать (порядок вычисления в setdefault)
    events.on_kill(101, e["def"].get("mob", "крыса"), zone)
    assert e["progress"] == 1 and e["contrib"][101] == 1

    mob = e["def"].get("mob", "крыса")
    for i in range(need - 1):
        events.on_kill(101 if i % 2 else 202, mob, zone)
    assert e["progress"] == need, f"счётчик не дошёл: {e['progress']}/{need}"
    assert set(e["contrib"]) == {101, 202}, e["contrib"]
    assert sum(e["contrib"].values()) == need, "вклады должны сходиться с целью"
    print("✓ общий счётчик копится всеми и сходится с вкладами")


def test_goal_completion_and_reward():
    """Достижение цели закрывает событие и отдаёт награду по уровню участника."""
    events.ENABLED = True
    events.reset()
    eid = _goal_event_id()
    zone = "Пепельные Пустоши"
    events.start(eid, zone=zone, world=World(), now=1000.0)
    e = events.active()[0]
    mob = e["def"].get("mob", "крыса")
    need = int(e["def"]["goal"]["count"])
    for _ in range(need):
        events.on_kill(101, mob, zone)

    done = events.take_completions()
    assert len(done) == 1 and done[0]["contrib"] == {101: need}, done
    assert events.take_completions() == [], "забирается насовсем, дважды не раздаём"

    # награда пропорциональна уровню участника, а не событию
    r5, r25 = events.goal_reward(5, done[0]["share"]), events.goal_reward(25, done[0]["share"])
    assert r25["xp"] > r5["xp"], (r5, r25)
    for lv in (5, 15, 25):
        need_xp = int(50 * lv * (1 + lv / 20))
        got = events.goal_reward(lv, done[0]["share"])["xp"]
        assert 0.2 <= got / need_xp <= 0.8, f"ур.{lv}: {got}/{need_xp} — вне разумной доли"

    # событие помечено на закрытие и закрывается ближайшим тиком
    w = World()
    events.tick(w, now=2000.0)
    assert not any(x["id"] == eid for x in events.active()), "событие должно закрыться"
    print("✓ цель закрывает событие, награда считается от уровня участника")


def test_goal_ignores_wrong_mob_and_zone():
    """Чужой моб и чужая зона в общий счётчик не идут."""
    events.ENABLED = True
    events.reset()
    eid = _goal_event_id()
    zone = "Пепельные Пустоши"
    events.start(eid, zone=zone, world=World(), now=1000.0)
    e = events.active()[0]
    mob = e["def"].get("mob")
    events.on_kill(101, mob, "Совсем другая зона")
    assert e["progress"] == 0, "убийство в чужой зоне не должно засчитываться"

    # goal без списка мобов = засчитывается ЛЮБОЙ моб зоны (так событие
    # работает на любом уровне) — проверяем именно это поведение
    events.on_kill(101, "__любой_моб__", zone)
    assert e["progress"] == 1, "без goal.mobs должен засчитываться любой моб зоны"

    # а когда список задан — отсечка по мобу работает
    e["def"] = dict(e["def"])
    e["def"]["goal"] = dict(e["def"]["goal"], mobs=["крыса"])
    events.on_kill(101, "__чужой_моб__", zone)
    assert e["progress"] == 1, "с goal.mobs чужой моб не должен засчитываться"
    events.on_kill(101, "крыса", zone)
    assert e["progress"] == 2, "нужный моб из goal.mobs засчитывается"
    print("✓ отсечка по зоне всегда; по мобу — когда список задан")


def test_goal_progress_visible():
    """Прогресс общей цели виден в баннере комнаты и на экране событий."""
    events.ENABLED = True
    events.reset()
    eid = _goal_event_id()
    zone = "Пепельные Пустоши"
    events.start(eid, zone=zone, world=World(), now=1000.0)
    e = events.active()[0]
    mob = e["def"].get("mob", "крыса")
    for _ in range(3):
        events.on_kill(101, mob, zone)
    need = int(e["def"]["goal"]["count"])
    assert f"3/{need}" in events.banner(), events.banner()
    assert f"3/{need}" in events.render(), events.render()
    print("✓ прогресс цели виден игроку (баннер + экран событий)")


def test_events_without_goal_unchanged():
    """События-множители продолжают работать как раньше (без счётчика)."""
    events.ENABLED = True
    events.reset()
    plain = next(eid for eid, d in events._DEFS.items() if not d.get("goal"))
    events.start(plain, world=World(), now=1000.0)
    e = events.active()[0]
    assert "progress" not in e and "contrib" not in e, e
    assert events.on_kill(101, "крыса", e.get("zone")) == []
    assert events.take_completions() == []
    print("✓ события без цели не изменились")


async def _group_kill(size, with_support=False):
    """Настоящий GameLoop: одно убийство, N бойцов; опционально лекарь и AFK."""
    from engine.character import Character
    from engine.content import WORLD
    from engine.loop import GameLoop

    events.ENABLED = True
    events.reset()
    world = World()
    mob = next(m for room, mobs in world.mobs.items() for m in mobs
               if WORLD.get(room, {}).get("zone") and not m.meta.get("boss"))
    zone = WORLD[mob.room]["zone"]
    events.start(_goal_event_id(), zone=zone, world=world, now=1000.0)
    event = events.active()[0]
    event["def"] = dict(event["def"])
    event["def"]["goal"] = dict(event["def"]["goal"], count=4)
    chars = {}
    active = range(1, size + 1)
    for uid in range(1, size + (2 if with_support else 1)):
        ch = Character(uid=uid, name=f"Участник{uid}", cls="priest" if uid == 2 else "warrior",
                       room=mob.room, level=10)
        ch.init_vitals()
        chars[uid] = ch
        if uid in active:
            mob.add_contrib(uid, 10)
    messages = []

    async def send(uid, text):
        messages.append((uid, text))

    async def no_save(*args, **kwargs):
        pass

    loop = GameLoop(world, chars, send, no_save)
    loop._check_levelup = no_save
    if with_support:
        class Party:
            def members(self, uid):
                return list(chars)
        loop.party_mgr = Party()
        killers = [chars[1]]  # лекаря с вкладом добавит пати; AFK не получит зачёт
    else:
        killers = list(chars.values())
    await loop.on_mob_death(mob, killers)
    assert event["progress"] == 1, f"Одно убийство группой {size} засчитано {event['progress']} раз"
    assert event["contrib"] == {uid: 1 for uid in active}, event["contrib"]
    rewards = [(uid, text) for uid, text in messages if "повержен" in text]
    assert {uid for uid, _ in rewards} == set(active), rewards
    assert all("1/4" in text for _, text in rewards), "Всем участникам показываем один общий прогресс"


def test_group_kill_counts_once_in_game_loop():
    for size in (1, 2, 4):
        asyncio.run(_group_kill(size))
    asyncio.run(_group_kill(2, with_support=True))
    print("✓ GameLoop: одно убийство = один зачёт для соло, дуо и группы; лекарь учтён, AFK исключён")


def test_group_goal_completion_includes_every_participant():
    events.ENABLED = True
    events.reset()
    zone = "Пепельные Пустоши"
    events.start(_goal_event_id(), zone=zone, world=World(), now=1000.0)
    event = events.active()[0]
    event["def"] = dict(event["def"])
    event["def"]["goal"] = dict(event["def"]["goal"], count=1)
    events.on_mob_kill([101, 202, 101, 303, 404], "крыса", zone)
    assert event["progress"] == 1
    assert event["contrib"] == {101: 1, 202: 1, 303: 1, 404: 1}
    done = events.take_completions()
    assert len(done) == 1 and done[0]["contrib"] == event["contrib"]
    assert events.on_mob_kill([505], "крыса", zone) == []
    assert events.take_completions() == [], "После достижения цели нет второго начисления"
    print("✓ Завершающее убийство учитывает всех участников, повторяющийся uid не удваивает вклад")


def test_empty_group_does_not_progress_goal():
    events.ENABLED = True
    events.reset()
    zone = "Пепельные Пустоши"
    events.start(_goal_event_id(), zone=zone, world=World(), now=1000.0)
    event = events.active()[0]
    assert events.on_mob_kill([], "крыса", zone) == []
    assert event["progress"] == 0 and event["contrib"] == {}
    print("✓ Без участников общая цель не продвигается")

if __name__ == "__main__":
    test_disabled_noop()
    test_start_and_modifiers()
    test_invasion_spawns_and_despawn()
    test_max_active_and_cooldown()
    test_announcements_have_ttl()
    test_goal_shared_counter()
    test_goal_completion_and_reward()
    test_goal_ignores_wrong_mob_and_zone()
    test_goal_progress_visible()
    test_events_without_goal_unchanged()
    test_group_kill_counts_once_in_game_loop()
    test_group_goal_completion_includes_every_participant()
    test_empty_group_does_not_progress_goal()
    events.ENABLED = False; events.reset()
    print("\n=== events OK ===")
