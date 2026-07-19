# -*- coding: utf-8 -*-
"""
Ежедневные задания: одно на день (ротация по дате), отслеживается в ch.flags["daily"].
Прогресс капает за убийства нужного моба; награда забирается раз в день у наставника.
"""
import hashlib
from datetime import date

from .content import MOBS, ITEMS
from . import content, money
from . import weekly

DAILY = content._load_optional("daily.yaml")


def _today():
    return date.today().isoformat()


def _pick(day: str) -> str:
    keys = list(DAILY)
    if not keys:
        return ""
    h = int(hashlib.md5(day.encode("utf-8")).hexdigest(), 16)
    return keys[h % len(keys)]


def ensure(ch):
    """Гарантировать актуальное ежедневное на сегодня (сброс при новом дне)."""
    today = _today()
    d = ch.flags.get("daily")
    if not d or d.get("date") != today or d.get("id") not in DAILY:
        ch.flags["daily"] = {"date": today, "id": _pick(today), "progress": 0, "claimed": False}
    return ch.flags["daily"]


def on_kill(ch, mob_id: str):
    if not DAILY:
        return None
    d = ensure(ch)
    q = DAILY.get(d["id"])
    if not q or q["type"] != "kill" or q["mob"] != mob_id:
        return None
    if d.get("claimed") or d["progress"] >= q["count"]:
        return None
    d["progress"] += 1
    if d["progress"] >= q["count"]:
        return f"📅 Ежедневное «{q['name']}» выполнено! Заберите награду у наставника."
    return None


def is_complete(ch) -> bool:
    if not DAILY:
        return False
    d = ensure(ch)
    q = DAILY.get(d["id"])
    return bool(q) and d["progress"] >= q["count"]


def claim(ch):
    """Забрать награду. -> строка-результат."""
    d = ensure(ch)
    q = DAILY.get(d["id"])
    if not q:
        return "Сегодня заданий нет."
    if d.get("claimed"):
        return "Награда за сегодня уже получена. Возвращайтесь завтра."
    if d["progress"] < q["count"]:
        return "Задание ещё не выполнено."
    rew = q.get("reward", {})
    ch.xp += rew.get("xp", 0)
    ch.gold += rew.get("gold", 0)
    for it in rew.get("items", []):
        ch.inventory.append(it)
    d["claimed"] = True
    parts = [f"{rew.get('xp',0)} опыта", money.fmt(rew.get("gold", 0))]
    if rew.get("items"):
        parts.append(", ".join(ITEMS.get(i, {}).get("name", i) for i in rew["items"]))
    result = "🎁 Награда получена: " + ", ".join(parts) + "."
    _wl = weekly.on_daily_claim(ch)
    if _wl:
        result += "\n" + _wl
    return result


def render(ch) -> str:
    if not DAILY:
        return "📅 Сегодня ежедневных заданий нет."
    d = ensure(ch)
    q = DAILY.get(d["id"])
    mob = MOBS.get(q["mob"], {}).get("name", q["mob"])
    status = "✅ выполнено" if d["progress"] >= q["count"] else f"{d['progress']}/{q['count']}"
    rew = q.get("reward", {})
    rparts = [f"{rew.get('xp',0)} опыта", money.fmt(rew.get("gold", 0))]
    if rew.get("items"):
        rparts.append(", ".join(ITEMS.get(i, {}).get("name", i) for i in rew["items"]))
    L = [f"📅 *Задание дня: {q['name']}*", "", f"_{q['desc']}_", "",
         f"🎯 Убить {mob}: {status}", f"🎁 Награда: {', '.join(rparts)}"]
    if d.get("claimed"):
        L.append("\n_Награда уже получена. Возвращайтесь завтра._")
    return "\n".join(L)
