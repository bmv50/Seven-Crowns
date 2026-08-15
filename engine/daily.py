# -*- coding: utf-8 -*-
"""
Ежедневные задания: одно на день (ротация по дате), отслеживается в ch.flags["daily"].
Прогресс капает за убийства нужного моба; награда забирается раз в день у наставника.

УРОВНЕВОЕ РАЗРЕШЕНИЕ ЦЕЛИ (2026-08-12). Раньше в daily.yaml был зашит конкретный
моб 1–3 ур., поэтому на 25 уровне ежедневка предлагала убить 8 крыс за 120 опыта
при потребности 2 640. Теперь запись в YAML — только обёртка (имя + текст), а
цель, счётчик и награда подбираются под уровень игрока: то же окно уровней и та
же формула награды, что у ИИ-поручений (engine/errands.py) — чтобы две системы
не разъезжались в балансе.

Разрешённая цель ФИКСИРУЕТСЯ в ch.flags["daily"] в момент выдачи. Это важно:
иначе левелап в середине дня менял бы цель под уже накопленным прогрессом.
Поэтому единая точка доступа к заданию — task_of(ch), а не DAILY[id]:
в DAILY лежит шаблон, у игрока — разрешённое задание.
"""
import hashlib
from datetime import date

from .content import MOBS, ITEMS
from . import content, money
from . import weekly
from . import errands as _err      # окно уровней, счётчик и формула награды

DAILY = content._load_optional("daily.yaml")

# Ежедневка выдаётся раз в день, поэтому платит щедрее повторяемого поручения,
# но остаётся «подталкиванием», а не основным источником: по замеру это ~1/3
# уровня на всём диапазоне 6–25 (ниже 6 ежедневки закрыты гейтом uigate).
DAILY_XP_MULT = 0.35
DAILY_GOLD_MULT = 0.40


def _today():
    return date.today().isoformat()


def _pick(day: str) -> str:
    keys = list(DAILY)
    if not keys:
        return ""
    h = int(hashlib.md5(day.encode("utf-8")).hexdigest(), 16)
    return keys[h % len(keys)]


def _target_for(level: int, day: str) -> str:
    """Цель дня для игрока этого уровня: моб из окна [level-2 .. level+3].

    Детерминирована по (дате, уровню) — все игроки одного уровня получают одну
    и ту же цель, то есть ежедневка остаётся общей темой дня, а не личным
    рандомом. Если в окне пусто (крайние уровни), окно расширяется.
    """
    lo = max(1, level - _err.LEVEL_LOW)
    hi = level + _err.LEVEL_HIGH
    pool = []
    for _ in range(40):        # расширяем окно, пока в нём никого нет
        pool = sorted(mid for mid, m in MOBS.items()
                      if not m.get("boss") and lo <= int(m.get("level", 1)) <= hi)
        if pool:
            break
        lo, hi = max(1, lo - 1), hi + 1
    if not pool:
        return ""
    h = int(hashlib.md5(f"{day}:{level}".encode("utf-8")).hexdigest(), 16)
    return pool[h % len(pool)]


def _resolve(ch, tpl_id: str, day: str) -> dict:
    """Разрешить шаблон в конкретное задание под уровень игрока.

    Запись с явным `mob` считается ЗАКРЕПЛЁННОЙ (authored-событие) и остаётся
    как есть — так YAML сохраняет возможность фиксированных заданий.
    """
    tpl = DAILY.get(tpl_id) or {}
    if tpl.get("mob"):
        return {"mob": tpl["mob"], "count": int(tpl.get("count", 1)),
                "reward": dict(tpl.get("reward") or {})}
    level = int(getattr(ch, "level", 1) or 1)
    mob = _target_for(level, day)
    m = MOBS.get(mob) or {}
    count = _err._kill_count(int(m.get("level", level)), level)
    return {
        "mob": mob,
        "count": count,
        "reward": {
            "xp": max(1, round(count * int(m.get("xp", 0)) * DAILY_XP_MULT)),
            "gold": max(1, round(count * int(m.get("gold", 0)) * DAILY_GOLD_MULT)),
            "items": [_err._consumable_for(level)],
        },
    }


def ensure(ch):
    """Гарантировать актуальное ежедневное на сегодня (сброс при новом дне)."""
    today = _today()
    d = ch.flags.get("daily")
    if not d or d.get("date") != today or d.get("id") not in DAILY or not d.get("mob"):
        tpl_id = _pick(today)
        ch.flags["daily"] = {"date": today, "id": tpl_id, "progress": 0, "claimed": False,
                             **_resolve(ch, tpl_id, today)}
    return ch.flags["daily"]


def task_of(ch) -> dict:
    """Разрешённое задание дня ЭТОГО игрока: {name, desc, type, mob, count, reward}.

    Единственная правильная точка доступа. Читать DAILY[id] напрямую нельзя —
    там лежит шаблон без цели и награды.
    """
    d = ensure(ch)
    tpl = DAILY.get(d["id"]) or {}
    if not d.get("mob"):
        return dict(tpl)
    mob_name = MOBS.get(d["mob"], {}).get("name", d["mob"])
    desc = str(tpl.get("desc", ""))
    try:
        desc = desc.format(mob=mob_name, count=d["count"])
    except (KeyError, IndexError, ValueError):
        pass                       # текст без плейсхолдеров — оставляем как есть
    return {**tpl, "desc": desc, "mob": d["mob"],
            "count": d["count"], "reward": d.get("reward") or {}}


def on_kill(ch, mob_id: str):
    if not DAILY:
        return None
    d = ensure(ch)
    q = task_of(ch)
    if not q or q.get("type") != "kill" or q.get("mob") != mob_id:
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
    q = task_of(ch)
    return bool(q.get("count")) and d["progress"] >= q["count"]


def claim(ch):
    """Забрать награду. -> строка-результат."""
    d = ensure(ch)
    q = task_of(ch)
    if not q or not q.get("mob"):
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
    q = task_of(ch)
    if not q.get("mob"):
        return "📅 Сегодня ежедневных заданий нет."
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
