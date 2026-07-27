# -*- coding: utf-8 -*-
"""
Тесты анти-личинга: награда за моба достаётся только тем, кто участвовал в бою.
Запуск:
    python test_antileech.py
Без БД и без сети — только engine/, никакого aiogram.

Что проверяем:
  1) учёт вклада: урон по мобу, полученный от моба урон (танк) и лечение
     союзника в бою — всё это засчитывается; лечение вне боя — нет;
  2) отсев в on_mob_death: афк-сопартиец не попадает в число получателей;
  3) главное следствие: доля активного игрока НЕ размывается личером —
     раньше пул делился на len(killers), и лишний рот резал долю вдвое;
  4) страховка: если вклада нет ни у кого (моб добит эффектом/скриптом),
     награда платится по-старому, а не теряется.
"""
import asyncio
import sys

from engine.world import MobInstance

_passed = 0
_failed = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✅ {name}")
    else:
        _failed += 1
        print(f"  ❌ {name}" + (f" — {extra}" if extra else ""))


# ───────────────────────── 1. учёт вклада ─────────────────────────

def test_contrib_accounting():
    print("\n1) Учёт вклада на экземпляре моба")
    from engine.content import MOBS
    mob_id = next(iter(MOBS))
    mob = MobInstance("room:x:0", mob_id, "room")

    check("свежий моб: вклада нет ни у кого", not mob.took_part(1))

    mob.add_contrib(1, 50)                 # дамагер ударил
    check("после урона вклад засчитан", mob.took_part(1))

    mob.add_contrib(2, 0)                  # промах/уклонение — нулевой урон
    check("нулевой вклад не засчитывается", not mob.took_part(2))

    mob.add_contrib(1, 25)
    check("вклад накапливается", mob.contrib[1] == 75.0, f"={mob.contrib[1]}")

    mob.add_contrib(3, -10)                # защита от отрицательных значений
    check("отрицательный вклад игнорируется", not mob.took_part(3))

    # threat и contrib — РАЗНЫЕ величины: threat домножается на классовый
    # множитель танка, contrib должен остаться честным.
    mob.add_threat(1, 999)
    check("threat не подменяет contrib", mob.contrib[1] == 75.0)


# ───────────────── 2–4. дележ награды в on_mob_death ─────────────────

class _FakePartyMgr:
    """Минимальный менеджер пати: все переданные uid в одной группе."""

    def __init__(self, uids):
        self._uids = list(uids)

    def members(self, uid):
        return [u for u in self._uids if u != uid]


def _make_char(uid, room, cls="warrior"):
    from engine.character import Character
    ch = Character(uid=uid, name=f"P{uid}", cls=cls, race="human")
    ch.room = room
    ch.hp = ch.max_hp
    ch.level = 10
    return ch


async def _kill_and_get_xp(active_uids, party_uids, contrib_uids):
    """Прогнать on_mob_death и вернуть {uid: набранный за убийство опыт}.

    active_uids  — кто попал в aggro-лист (то, что раньше и было killers);
    party_uids   — состав пати;
    contrib_uids — кто реально что-то сделал по мобу.
    """
    from engine.loop import GameLoop
    from engine.world import World
    from engine.content import MOBS

    mob_id = next(k for k, v in MOBS.items() if v.get("level", 1) <= 5)
    world = World()
    room = next(iter(world.mobs)) if world.mobs else "village"
    mob = MobInstance(f"{room}:{mob_id}:0", mob_id, room)
    for uid in contrib_uids:
        mob.add_contrib(uid, 100)

    chars = {uid: _make_char(uid, room) for uid in party_uids}
    for ch in chars.values():
        ch.room = mob.room

    gl = GameLoop.__new__(GameLoop)          # без сети и БД
    gl.world = world
    gl.chars = chars
    gl.party_mgr = _FakePartyMgr(party_uids)
    gl.on_combat_reward = None
    gl.send = lambda *a, **k: asyncio.sleep(0)
    gl.save = lambda *a, **k: asyncio.sleep(0)
    gl._check_levelup = lambda *a, **k: asyncio.sleep(0)

    before = {uid: chars[uid].xp for uid in party_uids}
    await gl.on_mob_death(mob, [chars[u] for u in active_uids])
    return {uid: chars[uid].xp - before[uid] for uid in party_uids}


def test_leech_excluded():
    print("\n2) Афк-сопартиец не получает долю")
    # 1 дрался, 2 стоял рядом в пати и не делал ничего
    gained = asyncio.run(_kill_and_get_xp(
        active_uids=[1], party_uids=[1, 2], contrib_uids=[1]))
    check("активный игрок получил опыт", gained[1] > 0, f"={gained[1]}")
    check("афк-сопартиец не получил ничего", gained[2] == 0, f"={gained[2]}")


def test_share_not_diluted():
    print("\n3) Личер не размывает долю активного")
    solo = asyncio.run(_kill_and_get_xp(
        active_uids=[1], party_uids=[1], contrib_uids=[1]))
    with_leech = asyncio.run(_kill_and_get_xp(
        active_uids=[1], party_uids=[1, 2], contrib_uids=[1]))
    check("доля активного не изменилась от присутствия личера",
          solo[1] == with_leech[1], f"соло={solo[1]}, с личером={with_leech[1]}")

    print("\n3b) Двое дрались — делят честно")
    both = asyncio.run(_kill_and_get_xp(
        active_uids=[1], party_uids=[1, 2], contrib_uids=[1, 2]))
    check("оба участника получили опыт", both[1] > 0 and both[2] > 0,
          f"={both}")
    check("вдвоём доля меньше, чем соло", both[1] < solo[1],
          f"вдвоём={both[1]}, соло={solo[1]}")


def test_no_contrib_fallback():
    print("\n4) Страховка: вклада нет ни у кого — награда не теряется")
    gained = asyncio.run(_kill_and_get_xp(
        active_uids=[1], party_uids=[1], contrib_uids=[]))
    check("награда выплачена по-старому", gained[1] > 0, f"={gained[1]}")


def main():
    print("=" * 56)
    print("АНТИ-ЛИЧИНГ: награда по вкладу в бою")
    print("=" * 56)
    test_contrib_accounting()
    test_leech_excluded()
    test_share_not_diluted()
    test_no_contrib_fallback()
    print("\n" + "=" * 56)
    print(f"Пройдено: {_passed}, провалено: {_failed}")
    print("=" * 56)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
