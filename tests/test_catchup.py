# -*- coding: utf-8 -*-
"""Тесты ленивой Catch-up симуляции. Запуск: python test_catchup.py"""
import time
from engine import catchup, npc_ai
from engine.world import World
from engine.content import WORLD


def _first_room_with_mob():
    w = World()
    for room, lst in w.mobs.items():
        if any(m.alive for m in lst):
            return w, room, next(m for m in lst if m.alive)
    raise RuntimeError("нет мобов")


def test_coarse_steps():
    s = catchup.coarse_steps(3600)
    assert s["combat"] == 360 and s["npc"] == 12 and s["resource"] == 2
    assert catchup.coarse_steps(-5)["combat"] == 0
    print("✓ coarse_steps (10с/5мин/30мин кванты)")


def test_active_set_cascade():
    # берём комнату с выходами
    room = next(r for r in WORLD if WORLD[r].get("exits"))
    act = catchup.active_set({room})
    assert room in act
    for nb in WORLD[room]["exits"].values():
        if nb in WORLD:
            assert nb in act          # соседи 1-го радиуса активны (преактивация)
    assert catchup.active_set(set()) == set()
    print("✓ active_set: игроки + соседи (каскад)")


def test_first_contact_no_false_respawn():
    catchup.reset()
    w, room, mob = _first_room_with_mob()
    # первый контакт: dt=0, ничего не мертво → ложного респавна нет
    res = catchup.catch_up_room(w, room, now=1000.0, life=npc_ai)
    assert res["dt"] == 0.0 and res["respawned"] == 0
    print("✓ первый контакт: dt=0, ложного респавна нет")


def test_lazy_respawn_on_activation():
    catchup.reset()
    w, room, mob = _first_room_with_mob()
    from engine.content import RESPAWN_SCALE
    respawn = mob.meta.get("respawn", 9999) * RESPAWN_SCALE
    t0 = 10_000.0
    catchup.catch_up_room(w, room, now=t0, life=npc_ai)   # регистрируем
    # моб умирает
    mob.dead_at = t0
    mob.hp = 0
    # игрок возвращается спустя > respawn — комната догоняется, моб воскресает
    res = catchup.catch_up_room(w, room, now=t0 + respawn + 5, life=npc_ai)
    assert res and res["respawned"] >= 1
    assert mob.alive and mob.hp == mob.max_hp and mob.dead_at is None
    print("✓ спящая комната догоняется: моб возрождён по абсолютному таймеру")


def test_needs_advanced_on_catchup():
    catchup.reset()
    w, room, mob = _first_room_with_mob()
    ai = npc_ai.get_ai(mob)
    ai.needs["boredom"] = 0.0
    catchup.catch_up_room(w, room, now=5000.0, life=npc_ai)
    catchup.catch_up_room(w, room, now=5000.0 + 600, life=npc_ai)  # +10 мин
    assert ai.needs["boredom"] > 0.0     # потребности подросли за ΔT
    print("✓ потребности NPC догнаны за ΔT")


def test_tick_only_active():
    catchup.reset()
    w, room, mob = _first_room_with_mob()
    out = catchup.tick(w, {room}, now=2000.0, life=npc_ai)        # первый раз — регистрация
    out2 = catchup.tick(w, {room}, now=2010.0, life=npc_ai)
    # обработаны только активные комнаты (room + соседи), не весь мир
    assert set(out2).issubset(catchup.active_set({room}))
    assert len(out2) <= len(catchup.active_set({room}))
    print("✓ tick догоняет только активные комнаты")


if __name__ == "__main__":
    test_coarse_steps()
    test_active_set_cascade()
    test_first_contact_no_false_respawn()
    test_lazy_respawn_on_activation()
    test_needs_advanced_on_catchup()
    test_tick_only_active()
    print("\n=== catchup OK ===")
