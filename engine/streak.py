# -*- coding: utf-8 -*-
"""
Стрик входов: серия дней подряд, отслеживается в ch.flags["streak"].
Раз в неделю пропуск одного дня прощается заморозкой (не сбрасывает серию).
На порогах (3/7/14/30 дней) выдаётся разовая награда — только при первом
достижении порога в текущей серии; при сбросе серии пороги открываются заново.
"""
from datetime import date, timedelta

from .content import ITEMS
from . import money

# порог_дней -> (флаг-выдача | None, предмет | None, золото, сообщение)
THRESHOLDS = {
    3: {"gold": 0, "items": [], "msg": "🔥 Стрик 3 дня: +10% опыта сегодня"},
    7: {"gold": 2000, "items": ["большое_зелье"], "msg": "🔥 Стрик 7 дней! Награда: {rew}"},
    14: {"gold": 8000, "items": ["эликсир"], "msg": "🔥 Стрик 14 дней! Награда: {rew}"},
    30: {"gold": 30000, "items": ["осколок_первотумана"], "msg": "🔥 Стрик 30 дней! Награда: {rew}"},
}


def _today(today=None) -> str:
    return today or date.today().isoformat()


def _iso_week(day: str) -> str:
    y, m, d = (int(x) for x in day.split("-"))
    iso = date(y, m, d).isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _yesterday(day: str) -> str:
    y, m, d = (int(x) for x in day.split("-"))
    return (date(y, m, d) - timedelta(days=1)).isoformat()


def _rew_text(cfg) -> str:
    parts = []
    if cfg.get("gold"):
        parts.append(money.fmt(cfg["gold"]))
    for it in cfg.get("items", []):
        parts.append(ITEMS.get(it, {}).get("name", it))
    return ", ".join(parts)


def _state(ch):
    s = ch.flags.get("streak")
    if not s:
        s = {"last": None, "days": 0, "freeze_week": None, "claimed_thresholds": []}
        ch.flags["streak"] = s
    s.setdefault("claimed_thresholds", [])
    return s


def touch(ch, today=None):
    """Отметить вход за сегодня. -> список строк-сообщений (может быть пустым)."""
    day = _today(today)
    s = _state(ch)
    if s["last"] == day:
        return []
    out = []
    if s["last"] == _yesterday(day):
        s["days"] += 1
    elif s["last"] is not None and _yesterday(_yesterday(day)) == s["last"] \
            and s.get("freeze_week") != _iso_week(day):
        # пропущен ровно один день, заморозка на этой ISO-неделе ещё не использована
        s["freeze_week"] = _iso_week(day)
        out.append("❄️ Заморозка стрика спасла серию")
    else:
        s["days"] = 1
        s["claimed_thresholds"] = []
    s["last"] = day
    out.extend(_check_thresholds(ch, s, day))
    return out


def _check_thresholds(ch, s, day):
    out = []
    d = s["days"]
    if d not in THRESHOLDS or d in s["claimed_thresholds"]:
        return out
    cfg = THRESHOLDS[d]
    s["claimed_thresholds"].append(d)
    if d == 3:
        ch.flags["streak_xp_until"] = day
        out.append(cfg["msg"])
        return out
    ch.gold += cfg.get("gold", 0)
    for it in cfg.get("items", []):
        ch.inventory.append(it)
    out.append(cfg["msg"].format(rew=_rew_text(cfg)))
    return out


def xp_mult(ch, today=None) -> float:
    day = _today(today)
    return 1.1 if ch.flags.get("streak_xp_until") == day else 1.0


def _next_threshold(days: int):
    for t in sorted(THRESHOLDS):
        if days < t:
            return t
    return None


def render(ch) -> str:
    s = _state(ch)
    days = s["days"]
    nxt = _next_threshold(days)
    week = _iso_week(s["last"]) if s["last"] else None
    freeze_ok = s.get("freeze_week") != week
    L = [f"🔥 *Стрик входов: {days} дн.*"]
    if nxt:
        L.append(f"Следующая награда на {nxt}-й день.")
    else:
        L.append("Все пороги наград в этой серии получены.")
    L.append("❄️ Заморозка на этой неделе: " + ("доступна" if freeze_ok else "уже использована"))
    return "\n".join(L)
