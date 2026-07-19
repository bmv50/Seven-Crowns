# -*- coding: utf-8 -*-
"""Генератор базового каталога оружия/брони по классам/весу/слотам/уровням.
Пишет data/items_gen.yaml (мержится в ITEMS). Редкость накручивается поверх
этих баз системой rarity (база#rarity)."""
import os, yaml

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
TIERS = [1, 15, 30, 45, 60, 75, 90]           # уровневые ступени баз
TIER_PREFIX = {1: "Грубый", 15: "Калёный", 30: "Закалённый", 45: "Рунный",
               60: "Мифрильный", 75: "Драконий", 90: "Эфирный"}

# оружие: weapon_class -> (эмодзи, существительное, базовый_атк_к1, множитель, втор.стат)
WEAPONS = {
    "sword":      ("🗡", "меч",        5, 1.0, None),
    "dagger":     ("🔪", "кинжал",     4, 0.8, "dex"),
    "mace":       ("🔨", "булава",     5, 1.05, "spi"),
    "axe":        ("🪓", "топор",      6, 1.1, "str"),
    "two_handed": ("⚔️", "двуручный меч", 7, 1.5, "str"),
    "staff":      ("🪄", "посох",      4, 0.9, "int"),
    "bow":        ("🏹", "лук",        5, 0.95, "dex"),
}
# броня: (вес) -> (factor, втор.стат), слоты
WEIGHTS = {"light": (0.7, "int"), "medium": (1.0, "dex"), "heavy": (1.4, "str")}
WEIGHT_NOUN = {"light": "роба", "medium": "кольчуга", "heavy": "латы"}
ARMOR_SLOTS = {
    "armor": ("🥋", "нагрудник", 1.0),
    "head":  ("⛑", "шлем", 0.5),
    "legs":  ("👖", "поножи", 0.7),
    "feet":  ("🥾", "сапоги", 0.4),
    "hands": ("🧤", "перчатки", 0.4),
}

items = {}


def add(key, **kw):
    items[key] = kw


for wc, (emo, noun, base, mult, sec) in WEAPONS.items():
    for t in TIERS:
        atk = int(round((base + t * 0.9) * mult))
        bonus = {"atk": atk}
        if sec:
            bonus[sec] = max(1, int(t * 0.15))
        nm = f"{TIER_PREFIX[t]} {noun}".strip().capitalize()
        add(f"g_{wc}_{t}", name=nm, emoji=emo, type="weapon", slot="weapon",
            weapon_class=wc, bonus=bonus, level_req=t, price=int(800 + atk * 220),
            desc=f"+{atk} к атаке. Уровень {t}+.")

# щиты (отдельный слот)
for t in TIERS:
    df = int(round(3 + t * 0.45))
    add(f"g_shield_{t}", name=f"{TIER_PREFIX[t]} щит", emoji="🛡", type="weapon",
        slot="shield", weapon_class="shield", bonus={"defense": df, "str": max(1, int(t*0.1))},
        level_req=t, price=int(700 + df * 240), desc=f"+{df} к защите. Уровень {t}+.")

# броня по весам/слотам
for weight, (factor, sec) in WEIGHTS.items():
    for slot, (emo, noun, sfac) in ARMOR_SLOTS.items():
        for t in TIERS:
            df = max(1, int(round((2 + t * 0.5) * factor * sfac)))
            bonus = {"defense": df, sec: max(1, int(t * 0.15 * sfac + 1))}
            wn = WEIGHT_NOUN[weight]
            nm = f"{TIER_PREFIX[t]} {noun} ({wn})"
            add(f"g_{weight}_{slot}_{t}", name=nm, emoji=emo, type="armor", slot=slot,
                armor_weight=weight, bonus=bonus, level_req=t,
                price=int(600 + df * 260), desc=f"+{df} к защите, {weight}. Уровень {t}+.")

out = os.path.join(DATA, "items_gen.yaml")
with open(out, "w", encoding="utf-8") as f:
    yaml.safe_dump(items, f, allow_unicode=True, sort_keys=True)
print(f"сгенерировано предметов: {len(items)} -> {out}")
# сводка
import collections
by_type = collections.Counter(v["type"] for v in items.values())
print("по типам:", dict(by_type))
