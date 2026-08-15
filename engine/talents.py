# -*- coding: utf-8 -*-
"""
Дерево талантов: очко за уровень, вкладывается в пассивные бонусы класса.
Хранится в ch.flags: talent_points (свободные), talents = {tid: ранг}.
Бонусы (atk_pct, hp_pct, crit, dmgred, lifesteal) применяются в Character.
"""
from . import content

TALENTS = content._load_optional("talents.yaml")


def for_class(cls):
    return {tid: t for tid, t in TALENTS.items() if t.get("class") == cls}


# Очко таланта выдаётся раз в TALENT_EVERY уровней. При капе 25 это даёт
# TALENT_BUDGET = 12 очков за прохождение — меньше, чем сумма рангов в дереве
# класса (15), поэтому вложить всё нельзя и выбор ветки становится настоящим.
TALENT_EVERY = 2


def talent_budget() -> int:
    """Сколько очков всего можно получить за одно прохождение до капа.
    ЖЁСТКИЙ потолок: реморт сбрасывает уровень в 1, и без этого потолка очки
    копились бы с каждым кругом (реморт — основная петля, круги повторяются)."""
    from .character import LEVEL_CAP
    return int(LEVEL_CAP) // TALENT_EVERY


def points_for_level(level: int) -> int:
    """Сколько очков талантов положено персонажу за достигнутый уровень.
    Очко раз в TALENT_EVERY уровней, не больше бюджета прохождения."""
    return min(int(level) // TALENT_EVERY, talent_budget())


def migrate_v2(ch):
    """Ленивая миграция на модель «очко каждые 4 уровня».
    Свободные легаси-очки (по 1 за уровень) пересчитываются: положено − вложено.
    Вложенные ранги НЕ трогаем — легаси-персонаж остаётся сильнее (осознанно)."""
    if ch.flags.get("talents_v2"):
        return
    due = points_for_level(int(getattr(ch, "level", 0) or 0))
    spent = sum(int(v) for v in (ch.flags.get("talents") or {}).values())
    ch.flags["talent_points"] = max(0, due - spent)
    ch.flags["talents_v2"] = True


def points(ch) -> int:
    return int(ch.flags.get("talent_points", 0))


def rank(ch, tid) -> int:
    return int((ch.flags.get("talents") or {}).get(tid, 0))


def invest(ch, tid):
    migrate_v2(ch)
    t = TALENTS.get(tid)
    if not t or t.get("class") != ch.cls:
        return False, "Это не талант вашего класса."
    if points(ch) <= 0:
        return False, "Нет свободных очков талантов."
    if rank(ch, tid) >= t.get("max_rank", 1):
        return False, "Талант уже изучен полностью."
    tal = ch.flags.setdefault("talents", {})
    tal[tid] = rank(ch, tid) + 1
    ch.flags["talent_points"] = points(ch) - 1
    return True, f"🌳 {t['name']} — ранг {tal[tid]}/{t['max_rank']}."


def reset(ch) -> int:
    spent = sum(int(v) for v in (ch.flags.get("talents") or {}).values())
    ch.flags["talents"] = {}
    ch.flags["talent_points"] = points(ch) + spent
    return spent


def bonus(ch, stat: str) -> float:
    total = 0.0
    for tid, rk in (ch.flags.get("talents") or {}).items():
        t = TALENTS.get(tid)
        if t:
            total += t.get("per_rank", {}).get(stat, 0) * int(rk)
    return total


def render(ch) -> str:
    migrate_v2(ch)
    L = [f"🌳 *Таланты* — свободных очков: {points(ch)}", ""]
    cls_talents = for_class(ch.cls)
    if not cls_talents:
        L.append("_Для вашего класса талантов пока нет._")
    for tid, t in cls_talents.items():
        rk = rank(ch, tid)
        mark = "🟢" if rk > 0 else "▫️"
        L.append(f"{mark} *{t['name']}* {rk}/{t['max_rank']} — _{t['desc']}_")
    from .character import LEVEL_CAP
    L.append(f"\n_Очко таланта — каждые {TALENT_EVERY} уровня ({talent_budget()} очков "
             f"к капу {LEVEL_CAP}; на всё дерево не хватит — выбирайте). Сброс бесплатный._")
    return "\n".join(L)
