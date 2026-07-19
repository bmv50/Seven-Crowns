# -*- coding: utf-8 -*-
"""Карма / PvP-метка (в духе Lineage 2 PK и Tibia skull).
Убийство мирного игрока (без PvP-метки) в открытой зоне даёт карму. Последствия
высокой кармы: стража враждебна в городах, торговцы отказывают, при смерти
выпадают вещи. Карма медленно угасает со временем.

Хранение: ch.flags["karma"] (int), ch.flags["pvp"] (bool — добровольная метка).
"""
import random
import time

CRIMINAL = 5      # порог «преступника»
OUTLAW = 15       # порог «изгоя»
KILL_KARMA = 5    # карма за убийство мирного
DECAY_STEP = 1    # сколько кармы спадает за интервал
DECAY_EVERY = 300 # секунд между списаниями кармы


def get(ch) -> int:
    return int(ch.flags.get("karma", 0))


def add(ch, n: int):
    ch.flags["karma"] = max(0, get(ch) + n)


def is_criminal(ch) -> bool:
    return get(ch) >= CRIMINAL


def is_outlaw(ch) -> bool:
    return get(ch) >= OUTLAW


def tier(ch):
    k = get(ch)
    if k >= OUTLAW:
        return "outlaw", "💀", "Изгой"
    if k >= CRIMINAL:
        return "criminal", "🔶", "Преступник"
    return "clean", "😇", "Чист"


# ── PvP-метка (PK): выдаётся за убийство игрока вне сейф-зоны, держится
# PK_DURATION, спадает сама либо снимается жрецом за деньги ──
PK_DURATION = 3 * 24 * 3600     # 3 дня
CLEAR_COST = 500000             # цена искупления у жреца (внутр.; money.fmt → 5000)


def pvp_marked(ch) -> bool:
    return time.time() < float(ch.flags.get("pk_until", 0))


def mark_pk(ch):
    ch.flags["pk_until"] = time.time() + PK_DURATION


def clear_mark(ch):
    ch.flags["pk_until"] = 0
    ch.flags["karma"] = 0


def mark_remaining(ch) -> int:
    return max(0, int(float(ch.flags.get("pk_until", 0)) - time.time()))


def remaining_human(ch) -> str:
    sec = mark_remaining(ch)
    if sec <= 0:
        return ""
    d, h, m = sec // 86400, (sec % 86400) // 3600, (sec % 3600) // 60
    if d:
        return f"{d}д {h}ч"
    return f"{h}ч {m}м" if h else f"{m}м"


def vendor_refuses(ch) -> bool:
    return is_outlaw(ch)


def guards_hostile(ch) -> bool:
    return is_outlaw(ch)


def death_drop_chance(ch) -> float:
    if is_outlaw(ch):
        return 0.5
    if is_criminal(ch):
        return 0.25
    return 0.0


# Мягкая смерть новичка: до этого уровня предметы при смерти не выпадают.
SOFT_DEATH_LEVEL = 5


def maybe_drop_on_death(ch):
    """С шансом по карме выронить случайный предмет из сумки. Возвращает item|None.
    Новичкам (уровень < SOFT_DEATH_LEVEL) — ничего не роняем (мягкая смерть)."""
    if getattr(ch, "level", 99) < SOFT_DEATH_LEVEL:
        return None
    if not ch.inventory:
        return None
    if random.random() < death_drop_chance(ch):
        item = random.choice(ch.inventory)
        ch.inventory.remove(item)
        return item
    return None


def on_pvp_kill(killer, victim, safe_zone=False):
    """Убийство игрока ВНЕ сейф-зоны → PvP-метка + карма убийце."""
    if safe_zone:
        return []   # в безопасной зоне PvP-убийства не караются меткой
    mark_pk(killer)
    add(killer, KILL_KARMA)
    _, emoji, label = tier(killer)
    return [f"☠️ Вы убили игрока вне безопасной зоны! Получена 🔴 PvP-метка "
            f"({remaining_human(killer)}). Статус: {emoji} {label}."]


def decay(ch):
    """Периодическое угасание кармы. Вызывать из игрового цикла."""
    if get(ch) <= 0:
        return
    last = ch.flags.get("karma_decay_ts", 0)
    now = time.time()
    if now - last >= DECAY_EVERY:
        ch.flags["karma_decay_ts"] = now
        add(ch, -DECAY_STEP)


def line(ch) -> str:
    k = get(ch)
    marked = pvp_marked(ch)
    if k <= 0 and not marked:
        return ""
    _, emoji, label = tier(ch)
    flag = f" 🔴PvP-метка ({remaining_human(ch)})" if marked else ""
    return f"{emoji} Карма: {k} ({label}){flag}"
