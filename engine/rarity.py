# -*- coding: utf-8 -*-
"""
Редкость предметов и таблица выпадения лута (модель «два броска»).
Редкость кодируется в ключе предмета как «база#rarity» (common — без суффикса).
Цвет в Telegram передаём эмодзи-кружком (шрифт красить нельзя); цветной фон —
на будущих картинках предметов.
"""
import random

# порядок по возрастанию силы
RARITY_ORDER = ["common", "green", "blue", "purple", "gold", "red"]
META = {
    "common": {"emoji": "⚪", "name": "Простая",      "mult": 1.0},
    "green":  {"emoji": "🟢", "name": "Редкая",       "mult": 1.3},
    "blue":   {"emoji": "🔵", "name": "Эпическая",    "mult": 1.7},
    "purple": {"emoji": "🟣", "name": "Мифическая",   "mult": 2.2},
    "gold":   {"emoji": "🟡", "name": "Легендарная",  "mult": 3.0},
    "red":    {"emoji": "🔴", "name": "Божественная", "mult": 4.0},
}

# Таблица: (min_lvl, max_lvl, шанс_дропа_экипировки, [(rarity, вес)…])
DROP_BANDS = [
    (1,   9,   0.18, [("common", 75), ("green", 22), ("blue", 3)]),
    (10,  29,  0.20, [("common", 18), ("green", 67), ("blue", 13), ("purple", 2)]),
    (30,  69,  0.22, [("green", 18), ("blue", 67), ("purple", 13), ("gold", 2)]),
    (70,  999, 0.25, [("blue", 18), ("purple", 70), ("gold", 12)]),
]
BOSS_RED_CHANCE = 0.15      # шанс божественной с рейд-босса


def split(key: str):
    """-> (base, rarity, seed|None). Ключ: «база#rarity» или «база#rarity#seed»."""
    if "#" in key:
        parts = key.split("#")
        base = parts[0]
        rar = parts[1] if len(parts) > 1 else "common"
        seed = parts[2] if len(parts) > 2 else None
        if rar in META:
            return base, rar, seed
    return key, "common", None


def base_of(key: str) -> str:
    return split(key)[0]


def rarity_of(key: str) -> str:
    return split(key)[1]


def seed_of(key: str):
    return split(key)[2]


def encode(base: str, rar: str, seed=None) -> str:
    if rar == "common":
        return base
    return f"{base}#{rar}#{seed}" if seed is not None else f"{base}#{rar}"


# ── аффиксы (доп. случайные бонусы на высоких редкостях) ──
AFFIX_STATS = ["str", "dex", "int", "spi", "atk", "defense", "crit"]
AFFIX_COUNT = {"purple": 1, "gold": 2, "red": 3}
AFFIX_RANGE = {"purple": (2, 5), "gold": (4, 9), "red": (8, 16)}
AFFIX_RU = {"str": "Сила", "dex": "Ловкость", "int": "Интеллект",
            "spi": "Дух", "atk": "Атака", "defense": "Защита", "crit": "Крит%"}


def affixes_for(rar: str, seed):
    """Детерминированный список (стат, величина) от редкости и сида."""
    n = AFFIX_COUNT.get(rar, 0)
    if not n or seed is None:
        return []
    rng = random.Random(int(seed))
    stats = rng.sample(AFFIX_STATS, min(n, len(AFFIX_STATS)))
    lo, hi = AFFIX_RANGE[rar]
    return [(s, rng.randint(lo, hi)) for s in stats]


def emoji(key: str) -> str:
    return META[rarity_of(key)]["emoji"]


def rarity_name(key: str) -> str:
    return META[rarity_of(key)]["name"]


def upgrade(rar: str) -> str:
    i = RARITY_ORDER.index(rar)
    return RARITY_ORDER[min(i + 1, len(RARITY_ORDER) - 1)]


def scaled_meta(base_meta: dict, rar: str, seed=None) -> dict:
    """Масштабировать характеристики под редкость + добавить аффиксы (по сиду)."""
    mult = META[rar]["mult"]
    m = dict(base_meta)
    bonus = {k: max(1, int(round(v * mult))) for k, v in (m.get("bonus") or {}).items()} \
        if isinstance(m.get("bonus"), dict) else {}
    affixes = affixes_for(rar, seed)
    for stat, amt in affixes:
        bonus[stat] = bonus.get(stat, 0) + amt
    if bonus:
        m["bonus"] = bonus
    if "effect" in m and isinstance(m["effect"], dict):
        m["effect"] = {k: (int(round(v * mult)) if isinstance(v, (int, float)) else v)
                       for k, v in m["effect"].items()}
    if "price" in m and isinstance(m["price"], (int, float)):
        m["price"] = int(m["price"] * mult * (1 + 0.25 * len(affixes)))
    m["name"] = f"{META[rar]['emoji']} {base_meta.get('name', '?')}"
    m["rarity"] = rar
    if affixes:
        m["affixes"] = [(AFFIX_RU.get(s, s), a) for s, a in affixes]
    return m


def _band(mob_level: int):
    for lo, hi, drop, weights in DROP_BANDS:
        if lo <= mob_level <= hi:
            return drop, weights
    return DROP_BANDS[-1][2], DROP_BANDS[-1][3]


def _weighted(weights):
    total = sum(w for _, w in weights)
    r = random.uniform(0, total)
    acc = 0
    for rar, w in weights:
        acc += w
        if r <= acc:
            return rar
    return weights[-1][0]


def roll_drop(mob_level: int, equip_pool, boss: bool = False, elite: bool = False):
    """Два броска: упал ли шмот → его редкость. Возвращает ключ base#rarity или None."""
    if not equip_pool:
        return None
    drop, weights = _band(mob_level)
    if elite:
        drop = min(0.95, drop * 2.2)
    if boss:
        drop = 1.0
    if random.random() > drop:
        return None
    rar = _weighted(weights)
    if elite:
        rar = upgrade(rar)
    if boss:
        rar = upgrade(rar)
        if random.random() < BOSS_RED_CHANCE:
            rar = "red"
    seed = random.randint(1, 10**9) if rar in AFFIX_COUNT else None
    return encode(random.choice(list(equip_pool)), rar, seed)
