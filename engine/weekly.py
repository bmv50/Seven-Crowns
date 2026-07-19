# -*- coding: utf-8 -*-
"""
Недельные задания: набор из 3 задач на ISO-неделю (ротация по неделе),
отслеживается в ch.flags["weekly"]. Прогресс капает за убийства (любые/боссы),
забор ежедневных наград и разнообразные не-килл цели (Этап 6.1: помощь
союзникам, групповые данжи, исследование мира, крафт, торговля на аукционе,
репутация фракций, точный урон по уязвимости, разговор во время события).
Награда забирается раз в неделю.
"""
import hashlib
from datetime import date

from .content import ITEMS
from . import content, money

WEEKLY = content._load_optional("weekly.yaml")

# Каталог известных движку типов недельных задач (для валидации data/weekly.yaml).
KNOWN_TYPES = frozenset({
    "kill_any", "kill_boss", "daily_claims",           # исходные (Этап 0)
    "heal_ally", "dungeon_group", "explore", "craft_item",
    "sell_lot", "faction_rep", "dtype_kill", "event_talk",   # Этап 6.1
})


def _today(today=None) -> str:
    return today or date.today().isoformat()


def _iso_week(today=None) -> str:
    day = _today(today)
    y, m, d = (int(x) for x in day.split("-"))
    iso = date(y, m, d).isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _pick(week: str) -> str:
    keys = list(WEEKLY)
    if not keys:
        return ""
    h = int(hashlib.md5(week.encode("utf-8")).hexdigest(), 16)
    return keys[h % len(keys)]


def ensure(ch, today=None):
    """Гарантировать актуальный недельный набор на текущую ISO-неделю (сброс при новой неделе)."""
    week = _iso_week(today)
    w = ch.flags.get("weekly")
    if not w or w.get("week") != week or w.get("id") not in WEEKLY:
        ch.flags["weekly"] = {"week": week, "id": _pick(week), "progress": {}, "claimed": False}
    return ch.flags["weekly"]


def _bump(w, s, task_id: str, amount: int = 1):
    """Продвинуть одну задачу набора s в состоянии w. -> строка при выполнении, иначе None."""
    task = next((t for t in s["tasks"] if t["id"] == task_id), None)
    if not task or w.get("claimed"):
        return None
    cur = int(w["progress"].get(task_id, 0))
    if cur >= task["count"]:
        return None
    cur = min(task["count"], cur + amount)
    w["progress"][task_id] = cur
    if cur >= task["count"]:
        return f"📌 Недельное «{task['name']}» выполнено!"
    return None


def on_kill(ch, mob_meta, today=None):
    """Инкремент прогресса недельных задач при убийстве моба. -> строка или None."""
    if not WEEKLY:
        return None
    w = ensure(ch, today)
    s = WEEKLY.get(w["id"])
    if not s:
        return None
    lines = []
    for t in s["tasks"]:
        l = None
        if t["type"] == "kill_any":
            l = _bump(w, s, t["id"], 1)
        elif t["type"] == "kill_boss" and mob_meta and mob_meta.get("boss"):
            l = _bump(w, s, t["id"], 1)
        if l:
            lines.append(l)
    if not lines:
        return None
    return "\n".join(lines)


def on_daily_claim(ch, today=None):
    """Инкремент прогресса задач типа daily_claims. -> строка или None."""
    if not WEEKLY:
        return None
    w = ensure(ch, today)
    s = WEEKLY.get(w["id"])
    if not s:
        return None
    lines = []
    for t in s["tasks"]:
        if t["type"] == "daily_claims":
            l = _bump(w, s, t["id"], 1)
            if l:
                lines.append(l)
    if not lines:
        return None
    return "\n".join(lines)


def _progress(ch, ttype: str, amount: int = 1, today=None):
    """Универсальный прогресс для не-килл типов задач недельника (Этап 6.1):
    бампает ВСЕ задачи набора типа ttype на amount. -> строка или None."""
    if not WEEKLY:
        return None
    w = ensure(ch, today)
    s = WEEKLY.get(w["id"])
    if not s:
        return None
    lines = []
    for t in s["tasks"]:
        if t["type"] == ttype:
            l = _bump(w, s, t["id"], amount)
            if l:
                lines.append(l)
    if not lines:
        return None
    return "\n".join(lines)


def on_heal_ally(ch, today=None):
    """Исцеление СОЮЗНИКА (не себя) скиллом. -> строка или None.
    Зовётся из engine/combat.py:use_skill (ветка heal/target=allies)."""
    return _progress(ch, "heal_ally", 1, today)


def on_dungeon_group(ch, today=None):
    """Босс подземелья убит В ГРУППЕ (≥2 killers). -> строка или None.
    Зовётся из engine/dungeon.py:on_kill(group=True)."""
    return _progress(ch, "dungeon_group", 1, today)


def on_room_visit(ch, room_id: str, today=None):
    """Посещение НОВОЙ для персонажа комнаты. -> строка или None.
    Повторное посещение прогресс не даёт (ch.flags['visited_rooms'] — история
    посещений, копится независимо от текущего недельного набора)."""
    visited = ch.flags.setdefault("visited_rooms", [])
    if room_id in visited:
        return None
    visited.append(room_id)
    return _progress(ch, "explore", 1, today)


def on_craft(ch, today=None):
    """Успешный крафт предмета у кузнеца. -> строка или None.
    Зовётся из engine/craft.py:craft() после успешной ковки."""
    return _progress(ch, "craft_item", 1, today)


def on_sell_lot(ch, today=None):
    """Лот на аукционе продан (у ПРОДАВЦА). -> строка или None.
    Зовётся из bot/main.py в момент зачисления выручки продавцу."""
    return _progress(ch, "sell_lot", 1, today)


def on_faction_rep(ch, amount: int, today=None):
    """Прирост репутации фракции (любой). -> строка или None.
    Зовётся из engine/reputation.py:gain() при amount > 0."""
    if amount <= 0:
        return None
    return _progress(ch, "faction_rep", amount, today)


def on_dtype_kill(ch, today=None):
    """Убийство моба типом урона, к которому он уязвим. -> строка или None.
    Зовётся из engine/loop.py, если mob.exploited_by содержит ch.uid
    (проставляется в engine/combat.py при попадании нужным dmg_type)."""
    return _progress(ch, "dtype_kill", 1, today)


def on_event_talk(ch, today=None):
    """Разговор с NPC во время активного мирового события. -> строка или None.
    Зовётся из talk-колбэка bot/main.py при events.ENABLED и events.active()."""
    return _progress(ch, "event_talk", 1, today)


def is_complete(ch, today=None) -> bool:
    if not WEEKLY:
        return False
    w = ensure(ch, today)
    s = WEEKLY.get(w["id"])
    if not s:
        return False
    return all(int(w["progress"].get(t["id"], 0)) >= t["count"] for t in s["tasks"])


def claim(ch, today=None):
    """Забрать награду за неделю. -> строка-результат."""
    w = ensure(ch, today)
    s = WEEKLY.get(w["id"])
    if not s:
        return "На этой неделе заданий нет."
    if w.get("claimed"):
        return "Награда за эту неделю уже получена. Возвращайтесь на следующей."
    if not is_complete(ch, today):
        return "Недельное задание ещё не выполнено."
    rew = s.get("reward", {})
    ch.xp += rew.get("xp", 0)
    ch.gold += rew.get("gold", 0)
    for it in rew.get("items", []):
        ch.inventory.append(it)
    w["claimed"] = True
    parts = [f"{rew.get('xp',0)} опыта", money.fmt(rew.get("gold", 0))]
    if rew.get("items"):
        parts.append(", ".join(ITEMS.get(i, {}).get("name", i) for i in rew["items"]))
    return "🎁 Награда получена: " + ", ".join(parts) + "."


def render(ch, today=None) -> str:
    if not WEEKLY:
        return "📌 На этой неделе заданий нет."
    w = ensure(ch, today)
    s = WEEKLY.get(w["id"])
    rew = s.get("reward", {})
    rparts = [f"{rew.get('xp',0)} опыта", money.fmt(rew.get("gold", 0))]
    if rew.get("items"):
        rparts.append(", ".join(ITEMS.get(i, {}).get("name", i) for i in rew["items"]))
    L = [f"📌 *Недельное: {s['name']}*", ""]
    for t in s["tasks"]:
        cur = int(w["progress"].get(t["id"], 0))
        status = "✅" if cur >= t["count"] else f"{cur}/{t['count']}"
        L.append(f"🎯 {t['name']}: {status}")
    L.append(f"🎁 Награда: {', '.join(rparts)}")
    if w.get("claimed"):
        L.append("\n_Награда уже получена. Возвращайтесь на следующей неделе._")
    return "\n".join(L)
