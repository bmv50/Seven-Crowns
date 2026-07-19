# -*- coding: utf-8 -*-
"""Ограничения экипировки: класс, уровень, вес брони, тип оружия.
Характеристики выводятся из названия предмета (инференс); явные поля
weapon_class / armor_weight / level_req / class_req в данных переопределяют.
"""
from .content import ITEMS, CLASSES
from . import rarity

# что класс может носить
CLASS_RULES = {
    "warrior":     {"weights": {"light", "medium", "heavy"},
                    "weapons": {"sword", "dagger", "mace", "axe", "two_handed", "bow", "staff"},
                    "shield": True},
    "paladin":     {"weights": {"light", "medium", "heavy"},
                    "weapons": {"sword", "mace", "two_handed", "staff"},
                    "shield": True},
    "rogue":       {"weights": {"light", "medium"},
                    "weapons": {"dagger", "sword", "bow"},
                    "shield": False},
    "priest":      {"weights": {"light", "medium"},
                    "weapons": {"mace", "staff"},
                    "shield": True},
    "mage":        {"weights": {"light"},
                    "weapons": {"staff", "dagger", "wand"},
                    "shield": False},
    "necromancer": {"weights": {"light"},
                    "weapons": {"staff", "dagger", "wand"},
                    "shield": False},
}
_DEFAULT_RULE = {"weights": {"light", "medium", "heavy"},
                 "weapons": {"sword", "dagger", "mace", "axe", "two_handed", "bow", "staff", "wand"},
                 "shield": True}

_WEAPON_KW = [
    ("two_handed", ["двуруч", "секира", "алебард", "бердыш", "большой", "великанский", "колун"]),
    ("staff",      ["посох", "жезл"]),
    ("wand",       ["волшебная палочка", "прут"]),
    ("dagger",     ["кинжал", "нож", "стилет", "коготь"]),
    ("bow",        ["лук", "арбалет"]),
    ("mace",       ["булав", "молот", "палиц", "кистен", "дубин"]),
    ("axe",        ["топор"]),
    ("sword",      ["меч", "клинок", "сабл", "тесак", "секач", "рапир"]),
]
_WEIGHT_KW = [
    ("heavy",  ["латы", "панцир", "кираса", "пластин", "тяжёл", "тяжел", "доспех глуб"]),
    ("medium", ["кольчуг", "чешуй", "ламелляр", "бригант", "куртка", "доспех"]),
    ("light",  ["кожан", "роб", "мантия", "ткан", "полотн", "плащ", "одеяние", "хитон"]),
]
_WEIGHT_SLOTS = {"armor", "head", "legs", "feet", "hands"}


def weapon_class(meta) -> str:
    if meta.get("weapon_class"):
        return meta["weapon_class"]
    low = (meta.get("name", "")).lower()
    for wc, kws in _WEAPON_KW:
        if any(k in low for k in kws):
            return wc
    return "sword"


def armor_weight(meta) -> str:
    if meta.get("armor_weight"):
        return meta["armor_weight"]
    low = (meta.get("name", "")).lower()
    for w, kws in _WEIGHT_KW:
        if any(k in low for k in kws):
            return w
    return "medium" if meta.get("slot") == "armor" else "light"


def level_req(meta) -> int:
    if meta.get("level_req"):
        return int(meta["level_req"])
    bonus = meta.get("bonus", {}) or {}
    total = sum(v for v in bonus.values() if isinstance(v, (int, float)))
    return max(1, int(total * 1.2))


def class_can_use(cls: str, key: str) -> bool:
    """Может ли класс носить предмет по правилам (без проверки уровня)."""
    if key not in ITEMS:
        return False
    meta = ITEMS[key]
    slot = meta.get("slot")
    if not slot:
        return False
    req = meta.get("class_req")
    if req and cls not in req:
        return False
    rule = CLASS_RULES.get(cls, _DEFAULT_RULE)
    if meta.get("type") == "weapon":
        if slot == "shield":
            return rule["shield"]
        return weapon_class(meta) in rule["weapons"]
    if slot == "shield":
        return rule["shield"]
    if slot in _WEIGHT_SLOTS:
        return armor_weight(meta) in rule["weights"]
    return True


def can_equip(ch, key: str):
    """(можно_ли, причина). key может быть «база#rarity»."""
    if key not in ITEMS:
        return False, "Нет такого предмета."
    meta = ITEMS[key]
    slot = meta.get("slot")
    if not slot:
        return False, "Это нельзя надеть."
    req = meta.get("class_req")
    if req and ch.cls not in req:
        return False, "Не для вашего класса."
    lr = level_req(meta)
    if ch.level < lr:
        return False, f"Нужен уровень {lr}."
    # реморт-предметы (вариант C): требуют N перерождений
    rr = int(meta.get("remort_req", 0) or 0)
    if rr and ch.remort_count < rr:
        return False, f"Требует перерождение {rr} ⭐"
    rule = CLASS_RULES.get(ch.cls, _DEFAULT_RULE)
    if meta.get("type") == "weapon":
        wc = weapon_class(meta)
        if slot == "shield":
            if not rule["shield"]:
                return False, f"{CLASSES[ch.cls]['name']} не может носить щит."
        elif wc not in rule["weapons"]:
            human = {"two_handed": "двуручное оружие", "shield": "щит", "staff": "посох",
                     "dagger": "кинжалы", "bow": "луки", "mace": "булавы",
                     "axe": "топоры", "sword": "мечи", "wand": "жезлы"}.get(wc, wc)
            return False, f"{CLASSES[ch.cls]['name']} не владеет: {human}."
    elif slot == "shield":
        if not rule["shield"]:
            return False, f"{CLASSES[ch.cls]['name']} не может носить щит."
    elif slot in _WEIGHT_SLOTS:
        aw = armor_weight(meta)
        if aw not in rule["weights"]:
            wn = {"heavy": "тяжёлую броню", "medium": "среднюю броню", "light": "лёгкую броню"}[aw]
            return False, f"{CLASSES[ch.cls]['name']} не может носить {wn}."
    return True, ""
