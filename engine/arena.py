# -*- coding: utf-8 -*-
"""
Рейтинговая арена: ELO-рейтинг за ранговые дуэли (в комнате-арене).
Рейтинг/статистика — в ch.flags["arena"] = {rating, wins, losses}.
"""
K = 32
START = 1000


def record(ch) -> dict:
    a = ch.flags.setdefault("arena", {})
    a.setdefault("rating", START)
    a.setdefault("wins", 0)
    a.setdefault("losses", 0)
    return a


def rating(ch) -> int:
    return int((ch.flags.get("arena") or {}).get("rating", START))


def has_played(ch) -> bool:
    a = ch.flags.get("arena") or {}
    return bool(a.get("wins") or a.get("losses"))


def _expected(a: int, b: int) -> float:
    return 1.0 / (1.0 + 10 ** ((b - a) / 400.0))


def update(winner, loser):
    """Обновить рейтинги после рангового боя. -> (Δпобедителя, Δпроигравшего)."""
    wa, la = record(winner), record(loser)
    rw, rl = wa["rating"], la["rating"]
    nw = round(rw + K * (1 - _expected(rw, rl)))
    nl = round(rl + K * (0 - _expected(rl, rw)))
    wa["rating"] = nw
    wa["wins"] += 1
    la["rating"] = max(0, nl)
    la["losses"] += 1
    return nw - rw, max(0, nl) - rl


def tier(r: int) -> str:
    if r >= 1600:
        return "💎 Алмаз"
    if r >= 1400:
        return "🟦 Платина"
    if r >= 1200:
        return "🥇 Золото"
    if r >= 1050:
        return "🥈 Серебро"
    return "🥉 Бронза"


def leaderboard(chars):
    played = [c for c in chars if has_played(c)]
    return sorted(played, key=lambda c: -rating(c))


def render_leaderboard(chars, me) -> str:
    L = ["🏟 *Таблица арены* — топ бойцов", ""]
    top = leaderboard(chars)[:10]
    if not top:
        L.append("_Пока никто не сражался на ранговой арене. Будьте первым!_")
    for i, c in enumerate(top, 1):
        a = record(c)
        L.append(f"{i}. *{c.name}* — {a['rating']} {tier(a['rating'])}  "
                 f"(🏆{a['wins']}/{a['losses']}💀)")
    a = record(me)
    L.append(f"\nВаш рейтинг: *{a['rating']}* {tier(a['rating'])}  "
             f"(🏆{a['wins']}/{a['losses']}💀)")
    return "\n".join(L)
