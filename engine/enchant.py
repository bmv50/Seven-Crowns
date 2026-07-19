# -*- coding: utf-8 -*-
"""Зачарование (улучшение +N) снаряжения у кузнеца.
Безопасно до +3, дальше риск провала: при неудаче выше безопасного уровень
снижается на 1 (механика «золотостока» в духе Lineage 2). Хранится в
ch.flags["ench"] = {"weapon": N, "armor": N}. Бонус суммируется в боёвке.
"""
import random

MAX_ENCH = 10
SAFE = 3
ATK_PER = 2      # +атаки за уровень зачар. оружия
DEF_PER = 1      # +защиты за уровень зачар. брони


def level(ch, slot: str) -> int:
    return int((ch.flags.get("ench") or {}).get(slot, 0))


def _set(ch, slot: str, v: int):
    ch.flags.setdefault("ench", {})[slot] = max(0, min(MAX_ENCH, int(v)))


def bonus_atk(ch) -> int:
    return level(ch, "weapon") * ATK_PER if ch.equipment.get("weapon") else 0


def bonus_def(ch) -> int:
    return level(ch, "armor") * DEF_PER if ch.equipment.get("armor") else 0


def cost(cur: int) -> int:
    """Стоимость попытки перейти с +cur на +cur+1 (в бронзе)."""
    return 20000 * (cur + 1)


def success_chance(cur: int) -> float:
    if cur < SAFE:
        return 1.0
    return max(0.25, 1.0 - (cur - SAFE + 1) * 0.15)


def attempt(ch, slot: str):
    """Вернёт (status, new_level). status: max/empty/poor/ok/fail/fail_down."""
    if slot not in ("weapon", "armor"):
        return ("empty", 0)
    if not ch.equipment.get(slot):
        return ("empty", 0)
    cur = level(ch, slot)
    if cur >= MAX_ENCH:
        return ("max", cur)
    c = cost(cur)
    if ch.gold < c:
        return ("poor", cur)
    ch.gold -= c
    if random.random() < success_chance(cur):
        _set(ch, slot, cur + 1)
        return ("ok", cur + 1)
    if cur > SAFE:
        _set(ch, slot, cur - 1)
        return ("fail_down", cur - 1)
    return ("fail", cur)
