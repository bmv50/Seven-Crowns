# -*- coding: utf-8 -*-
"""
Ленивая симуляция мира (Catch-up), адаптация из референса TeleMud.

Идея: не тикать каждую комнату каждую секунду. Комнаты делятся на «активные»
(где есть игроки + соседи 1-го радиуса — каскадная преактивация) и «спящие».
Спящие не симулируются; когда игрок входит в спящую комнату, она «догоняется»:
прошедшее время ΔT с последнего тика применяется одним махом — респавн мобов
(по абсолютному dead_at — корректно при любом ΔT) и рост потребностей NPC
(закрытая форма npc_ai.update_needs).

Живёт за флагом ENABLED. При выключенном — поведение игры не меняется (loop
тикает мир как раньше). При включённом — loop обрабатывает только активные
комнаты (CPU-экономия), а спящие догоняются при активации.
"""
import time
from typing import Dict, List, Optional, Set

from .content import (WORLD, RESPAWN_SCALE,
                      STARTER_MAX_LEVEL, STARTER_RESPAWN_SCALE)

ENABLED = False

# крупные кванты времени (секунды) — как в референсе: бой/NPC/ресурсы
COARSE_COMBAT = 10
COARSE_NPC = 300
COARSE_RESOURCE = 1800

# когда комната последний раз досимулирована до реального времени
_last: Dict[str, float] = {}


def reset():
    _last.clear()


def coarse_steps(dt: float) -> Dict[str, int]:
    """Разбить ΔT на крупные кванты (для отчёта/итеративных процессов)."""
    if dt < 0:
        dt = 0
    return {
        "combat": int(dt // COARSE_COMBAT),
        "npc": int(dt // COARSE_NPC),
        "resource": int(dt // COARSE_RESOURCE),
    }


def neighbors(room: str) -> List[str]:
    return [d for d in WORLD.get(room, {}).get("exits", {}).values() if d in WORLD]


def active_set(occupied) -> Set[str]:
    """Активные комнаты = где игроки + соседи 1-го радиуса (преактивация)."""
    active: Set[str] = set()
    for r in occupied:
        active.add(r)
        for nb in neighbors(r):
            active.add(nb)
    return active


def respawn_room(world, room: str, now: float) -> int:
    """Возродить мобов в комнате по абсолютному таймеру. Вернуть число возрождённых."""
    n = 0
    returned = []
    for inst in list(world.mobs.get(room, [])):
        if inst.dead_at is not None:
            lvl = inst.meta.get("level", 1)
            scale = STARTER_RESPAWN_SCALE if lvl <= STARTER_MAX_LEVEL else RESPAWN_SCALE
            respawn = inst.meta.get("respawn", 9999) * scale
            if now - inst.dead_at >= respawn:
                inst.hp = inst.max_hp
                inst.dead_at = None
                inst.last_tick = now
                inst.aggro = []
                inst.effects = []
                if hasattr(inst, "threat"):
                    inst.threat.clear()
                if getattr(inst, "home", room) != room and hasattr(world, "_relocate"):
                    returned.append(inst)
                n += 1
    for inst in returned:
        world._relocate(inst, inst.home)
    return n


def _advance_needs(world, room: str, dt: float, life) -> None:
    """Догнать потребности NPC в комнате на ΔT (закрытая форма, без пошага)."""
    if life is None or dt <= 0:
        return
    for inst in world.mobs.get(room, []):
        if not inst.alive:
            continue
        ai = life.get_ai(inst)
        life.update_needs(ai, dt)
        ai.last_update = time.time()


def catch_up_room(world, room: str, now: float = None, life=None) -> dict:
    """
    Догнать одну комнату до now. Возвращает сводку {dt, steps, respawned}.
    При первом контакте dt=0 (ретроспективно потребности не растим), но респавн
    по абсолютному dead_at идёт всегда — комната оживает при первом же входе.
    """
    now = now or time.time()
    prev = _last.get(room)
    _last[room] = now
    respawned = respawn_room(world, room, now)
    dt = (now - prev) if prev is not None else 0.0
    if dt > 0:
        _advance_needs(world, room, dt, life)
    return {"dt": dt, "steps": coarse_steps(dt), "respawned": respawned}


def tick(world, occupied, now: float = None, life=None) -> Dict[str, dict]:
    """
    Догнать все активные комнаты (игроки + соседи). Возвращает {room: summary}.
    Спящие комнаты не трогаются — они догонятся при следующей активации.
    """
    now = now or time.time()
    out: Dict[str, dict] = {}
    for room in active_set(occupied):
        out[room] = catch_up_room(world, room, now, life)
    return out
