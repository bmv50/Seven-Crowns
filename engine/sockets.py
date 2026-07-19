# -*- coding: utf-8 -*-
"""Сокеты и руны. Гнёзда появляются на предметах от синей редкости; их число
растёт с редкостью. В гнёзда вставляются руны (предметы type=rune с rune_bonus).
Вставленные руны хранятся по СЛОТУ экипировки: ch.flags["sockets"][slot] = [rune_key…].
Бонусы суммируются в характеристики (как зачар)."""
from .content import ITEMS
from . import rarity

# число гнёзд по редкости предмета
SOCKETS_BY_RARITY = {"common": 0, "green": 0, "blue": 1, "purple": 2, "gold": 3, "red": 4}


def socket_count(item_key: str) -> int:
    if not item_key:
        return 0
    return SOCKETS_BY_RARITY.get(rarity.rarity_of(item_key), 0)


def _store(ch) -> dict:
    return ch.flags.setdefault("sockets", {})


def slot_runes(ch, slot: str) -> list:
    return _store(ch).get(slot, [])


def free_sockets(ch, slot: str) -> int:
    item = ch.equipment.get(slot)
    return max(0, socket_count(item) - len(slot_runes(ch, slot)))


def is_rune(key: str) -> bool:
    return ITEMS.get(rarity.base_of(key), {}).get("type") == "rune"


def socket(ch, slot: str, rune_key: str):
    """Вставить руну из сумки в гнездо предмета на слоте. (ok, сообщение)."""
    item = ch.equipment.get(slot)
    if not item:
        return False, "В этом слоте нет предмета."
    if socket_count(item) == 0:
        return False, "У предмета нет гнёзд (нужна синяя редкость или выше)."
    if free_sockets(ch, slot) <= 0:
        return False, "Все гнёзда заняты."
    if rune_key not in ch.inventory or not is_rune(rune_key):
        return False, "Нет такой руны в сумке."
    ch.inventory.remove(rune_key)
    _store(ch).setdefault(slot, []).append(rune_key)
    return True, f"Руна вставлена: {ITEMS.get(rune_key, {}).get('name', rune_key)}."


def clear_slot(ch, slot: str):
    """Выбить руны из слота (руны теряются — как в L2). Возвращает число."""
    runes = _store(ch).pop(slot, [])
    return len(runes)


def stat_bonus(ch, stat: str) -> int:
    """Суммарный бонус к характеристике stat от всех вставленных рун."""
    total = 0
    store = ch.flags.get("sockets", {})
    for slot, runes in store.items():
        if not ch.equipment.get(slot):
            continue
        for rk in runes:
            total += ITEMS.get(rk, {}).get("rune_bonus", {}).get(stat, 0)
    return total


def render(ch) -> str:
    lines = ["💎 *Сокеты снаряжения*"]
    store = ch.flags.get("sockets", {})
    any_slot = False
    for slot in ("weapon", "armor", "shield", "head", "legs", "hands", "feet"):
        item = ch.equipment.get(slot)
        if not item:
            continue
        cnt = socket_count(item)
        if cnt == 0:
            continue
        any_slot = True
        used = store.get(slot, [])
        marks = " ".join(ITEMS.get(r, {}).get("emoji", "💠") for r in used)
        marks += " ▫️" * (cnt - len(used))
        lines.append(f"{ITEMS[item]['name']}: [{marks.strip()}] ({len(used)}/{cnt})")
    if not any_slot:
        lines.append("Нет надетых предметов с гнёздами (синие+).")
    return "\n".join(lines)
