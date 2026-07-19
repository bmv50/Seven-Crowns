# -*- coding: utf-8 -*-
"""
Крафт у кузнеца. Чистая логика без Telegram (тестируется напрямую).
Состояние берётся из Character (инвентарь + золото).
"""
from typing import List, Tuple

from .content import RECIPES, ITEMS
from . import money
from . import weekly
from .character import Character


def recipes_at(station: str) -> List[str]:
    """ID рецептов, доступных у данной станции (NPC)."""
    return [rid for rid, r in RECIPES.items() if r.get("station") == station]


def missing_inputs(ch: Character, rid: str) -> List[str]:
    """Чего не хватает для крафта (материалы + золото). Пусто = можно ковать."""
    r = RECIPES[rid]
    lack = []
    for item_key, qty in r.get("inputs", []):
        have = ch.inventory.count(item_key)
        if have < qty:
            lack.append(f"{ITEMS[item_key]['name']} {have}/{qty}")
    if ch.gold < r.get("gold", 0):
        lack.append(f"деньги {money.fmt(ch.gold)}/{money.fmt(r['gold'])}")
    return lack


def can_craft(ch: Character, rid: str) -> bool:
    return rid in RECIPES and not missing_inputs(ch, rid)


def craft(ch: Character, rid: str) -> Tuple[bool, str]:
    """Сковать предмет: списать материалы и золото, выдать результат."""
    if rid not in RECIPES:
        return False, "Нет такого рецепта."
    lack = missing_inputs(ch, rid)
    if lack:
        return False, "🔨 Не хватает: " + ", ".join(lack)
    r = RECIPES[rid]
    for item_key, qty in r.get("inputs", []):
        for _ in range(qty):
            ch.inventory.remove(item_key)
    ch.gold -= r.get("gold", 0)
    out = r["output"]
    ch.inventory.append(out)
    msg = f"🔨 Сковано: {ITEMS[out]['name']}! (Осталось: {money.fmt(ch.gold)})"
    _wl = weekly.on_craft(ch)
    if _wl:
        msg += "\n" + _wl
    return True, msg
