# -*- coding: utf-8 -*-
"""
Разборка снаряжения в ресурс: «ненужный лут → туманная пыль» (этап 6.2).

Зачем: вдоль прокачки падает много экипировки не своего класса/слабее носимой
(см. sim_loot_cadence — до половины забегов проходят окно без апгрейда оружия).
Раньше такой лут либо пылился в сумке, либо продавался за копейки (SELL_RATE_EQUIP
= 3%). Теперь его можно РАЗОБРАТЬ прямо в поле (не у кузнеца — утилизация часть
цикла) в «туманную пыль», а из пыли сконденсировать гарантированный предмет окна
(pity-крафт, data/recipes.yaml). Пыль НЕ продаётся (price 0) — её ценность только
в крафте, поэтому фонтан золота исключён.

Формула количества пыли: level_req // 4 + бонус за редкость. Редкие/высокоуровневые
предметы дают больше пыли — так пыль отражает «вес» разобранного лута.
"""
from typing import Tuple

from .content import ITEMS
from . import rarity
from . import equip as _equip

DUST_ITEM = "туманная_пыль"

# Бонус пыли за редкость (коды из rarity.META). Обычные — без бонуса, ценность
# растёт к божественным. Совпадает с sim_loot_cadence.RARITY_DUST.
RARITY_DUST = {"common": 0, "green": 1, "blue": 3, "purple": 8, "gold": 13, "red": 20}

# типы, которые можно разбирать (только снаряжение)
_EQUIP_TYPES = ("weapon", "armor", "accessory")


def dust_for(item_key: str) -> int:
    """Сколько пыли даёт разборка предмета (ключ может быть «база#rarity#seed»)."""
    base, rar, _ = rarity.split(item_key)
    meta = ITEMS.get(base)
    if not meta:
        return 1
    lr = _equip.level_req(meta)
    return max(1, lr // 4 + RARITY_DUST.get(rar, 0))


def can_salvage(ch, item_key: str) -> Tuple[bool, str]:
    """(можно_ли, причина). Разбирать можно только не надетую экипировку из сумки,
    не квестовую и не саму пыль/материалы."""
    base = rarity.base_of(item_key)
    meta = ITEMS.get(base)
    if not meta:
        return False, "Нет такого предмета."
    if meta.get("type") not in _EQUIP_TYPES:
        return False, "Разобрать можно только снаряжение."
    # надетое не разбираем (проверяем и точный ключ, и базу — на всякий)
    equipped = set(v for v in ch.equipment.values() if v)
    if item_key in equipped:
        return False, "Сначала снимите предмет."
    # в сумке должен быть хотя бы один НЕ надетый экземпляр
    have = ch.inventory.count(item_key)
    reserved = 1 if item_key in equipped else 0
    if have - reserved <= 0:
        return False, "Этого предмета нет в сумке."
    return True, ""


def salvage(ch, item_key: str) -> Tuple[bool, str, int]:
    """Разобрать предмет: удалить из сумки, начислить пыль. -> (ok, сообщение, пыль)."""
    ok, why = can_salvage(ch, item_key)
    if not ok:
        return False, why, 0
    dust = dust_for(item_key)
    ch.inventory.remove(item_key)
    for _ in range(dust):
        ch.inventory.append(DUST_ITEM)
    name = ITEMS.get(item_key, {}).get("name", item_key)
    dust_name = ITEMS.get(DUST_ITEM, {}).get("name", DUST_ITEM)
    return True, f"🔨 Разобрано: {name} → +{dust} {dust_name}.", dust
