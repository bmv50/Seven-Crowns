# -*- coding: utf-8 -*-
"""
Сезоны/лиги: периодический ладдер с очками, сбросом и наградами по рангу.

За флагом ENABLED. Сезон длится SEASON_LENGTH (по умолчанию неделя). Очки
начисляются за убийства (loop.on_mob_death) в ch.flags["season"]. При смене
сезона (ensure) накопленные очки конвертируются в награду по рангу, лучший
результат сохраняется, счёт обнуляется. Всё хранится в ch.flags (персистентно).

Сезонный трек (TRACK): параллельная лесенка наград за очки сезона (ch.flags
["season_track"]), не зависит от лиги/ролловера ensure(). Незабранные ступени
сгорают при смене сезона — стимул забрать награды до конца недели.
"""
import time

from .content import ITEMS
from . import money
from . import chronicle

ENABLED = False
SEASON_LENGTH = 7 * 86400     # неделя

# пороги очков -> (название лиги, эмодзи, награда-золото при ролловере)
# Калибровка экономики: сезон = неделя, поэтому награда лиги при ролловере
# сверялась с недельным доходом активного игрока и стоимостью топ-предметов.
# Прежние суммы (Алмаз 400k / Легенда 1M внутр.) были ~в 2× выше топ-предмета
# игры (венец_семи_корон 400k внутр.); одна лига-выплата спайкала капитал.
# Срезано пропорционально: верхняя лига ≈ 1.2× топ-предмета (~2-3 дня фарма),
# нижние — линейно. Онбординг лиг не касается (сезоны за флагом ENABLED).
TIERS = [
    (0,     "Бронза",   "🥉", 5000),
    (500,   "Серебро",  "🥈", 15000),
    (2000,  "Золото",   "🥇", 40000),
    (6000,  "Платина",  "💠", 120000),
    (15000, "Алмаз",    "💎", 240000),
    (40000, "Легенда",  "👑", 480000),
]


def season_id(now=None) -> int:
    return int((now or time.time()) // SEASON_LENGTH)


def _sd(ch):
    return ch.flags.setdefault("season", {"id": season_id(), "pts": 0, "best": 0})


def tier(pts: int):
    name, emoji = "Бронза", "🥉"
    for thr, n, e, _g in TIERS:
        if pts >= thr:
            name, emoji = n, e
    return name, emoji


def _reward_for(pts: int) -> int:
    g = 0
    for thr, _n, _e, gold in TIERS:
        if pts >= thr:
            g = gold
    return g


def ensure(ch, now=None):
    """Проверить смену сезона. При ролловере начислить награду и вернуть её dict (иначе None)."""
    sid = season_id(now)
    d = _sd(ch)
    if d.get("id") == sid:
        return None
    pts = int(d.get("pts", 0))
    reward = None
    if pts > 0:
        gold = _reward_for(pts)
        name, emoji = tier(pts)
        ch.gold += gold
        reward = {"pts": pts, "gold": gold, "tier": name, "emoji": emoji}
    d["best"] = max(int(d.get("best", 0)), pts)
    d["id"] = sid
    d["pts"] = 0
    # хроника: «Начался сезон N» — ровно одна запись на sid (дедуп record_once),
    # даже если ensure() вызовут для многих персонажей при одном и том же ролловере.
    chronicle.record_once("season", str(sid), f"Начался сезон {sid}")
    return reward


def add_points(ch, n: int, now=None):
    if not ENABLED or n <= 0:
        return None
    rew = ensure(ch, now)          # на случай смены сезона между сессиями
    d = _sd(ch)
    d["pts"] = int(d.get("pts", 0)) + int(n)
    # хроника: игрок впервые за сезон вошёл в высшую лигу «Легенда» (порог —
    # последняя ступень TIERS). Дедуп по (sid, uid) — запись ровно одна за сезон.
    _legend_thr = TIERS[-1][0]
    if d["pts"] >= _legend_thr:
        chronicle.record_once("legend", f"{d['id']}:{getattr(ch, 'uid', 0)}",
                              f"{getattr(ch, 'name', 'Игрок')} вошёл в лигу Легенда!")
    return rew


def points(ch) -> int:
    return int(_sd(ch).get("pts", 0))


def time_left(now=None) -> int:
    now = now or time.time()
    return int((season_id(now) + 1) * SEASON_LENGTH - now)


def render(ch) -> str:
    d = _sd(ch)
    pts = points(ch)
    name, emoji = tier(pts)
    left = time_left()
    days, hrs = left // 86400, (left % 86400) // 3600
    nxt = next(((thr, n) for thr, n, _e, _g in TIERS if thr > pts), None)
    L = [f"🏅 *Сезон {d['id']}* — лига {emoji} *{name}*",
         f"Очки сезона: *{pts}*  ·  лучший результат: {d.get('best', 0)}",
         f"До конца сезона: {days}д {hrs}ч"]
    if nxt:
        L.append(f"До следующей лиги «{nxt[1]}»: ещё {nxt[0] - pts} очков")
    L.append("_Очки идут за убийства; в конце сезона — награда по лиге и сброс._")
    return "\n".join(L)


def leaderboard(chars, me=None) -> str:
    rows = sorted(chars, key=lambda c: points(c), reverse=True)[:10]
    L = ["🏆 *Таблица сезона (топ-10):*", ""]
    for i, c in enumerate(rows, 1):
        nm, em = tier(points(c))
        mark = " ⬅️ вы" if me is not None and c.uid == me.uid else ""
        L.append(f"{i}. {em} {c.name} — {points(c)} очк. ({nm}){mark}")
    if not rows:
        L.append("_Пока пусто — заработайте очки в бою._")
    return "\n".join(L)


# ───────────────────── Сезонный трек (лесенка наград за очки) ─────────────────────
# 10 ступеней: (порог очков, награда {gold, items}). Награды забираются по ходу
# сезона, не привязаны к лиге/ролловеру; при смене сезона незабранные сгорают.
TRACK = [
    (100,   {"gold": 2000}),
    (250,   {"items": ["большое_зелье"]}),
    (500,   {"gold": 6000}),
    (1000,  {"items": ["эликсир"]}),
    (1500,  {"gold": 12000}),
    (2500,  {"items": ["большое_зелье", "зелье_маны"]}),
    (4000,  {"gold": 25000}),
    (6000,  {"items": ["эликсир", "большое_зелье"]}),
    (10000, {"gold": 60000}),
    (15000, {"items": ["осколок_первотумана"]}),
]


def _track(ch, now=None):
    """Состояние трека; сбрасывается (вместе с claimed) при смене сезона."""
    sid = season_id(now)
    t = ch.flags.setdefault("season_track", {"id": sid, "claimed": []})
    if t.get("id") != sid:
        t["id"] = sid
        t["claimed"] = []          # незабранные ступени сгорают
    return t


def _reward_str(rew: dict) -> str:
    parts = []
    if rew.get("gold"):
        parts.append(money.fmt(rew["gold"]))
    if rew.get("items"):
        parts.append(", ".join(ITEMS.get(i, {}).get("name", i) for i in rew["items"]))
    return ", ".join(parts)


def track_claimable(ch, now=None):
    """Пороги очков, уже достигнутые, но ещё не забранные (список int)."""
    t = _track(ch, now)
    claimed = set(t.get("claimed", []))
    pts = points(ch)
    return [thr for thr, _rew in TRACK if pts >= thr and thr not in claimed]


def track_claim(ch, thr: int, now=None) -> str:
    """Забрать награду ступени thr. -> строка-результат (успех/отказ)."""
    t = _track(ch, now)
    rew = next((r for th, r in TRACK if th == thr), None)
    if rew is None:
        return "Такой ступени нет."
    if thr in t.get("claimed", []):
        return "Уже забрано."
    if points(ch) < thr:
        return "Ещё не достигнуто."
    ch.gold += rew.get("gold", 0)
    for it in rew.get("items", []):
        ch.inventory.append(it)
    t.setdefault("claimed", []).append(thr)
    return f"🎁 Награда за {thr} очк. получена: {_reward_str(rew)}."


def track_render(ch, now=None) -> str:
    """Прогресс по ступеням трека: ✅ забрано / 🎁 доступно / 🔒 впереди."""
    t = _track(ch, now)
    claimed = set(t.get("claimed", []))
    pts = points(ch)
    L = ["🎫 *Сезонный трек* — награды за очки сезона:", ""]
    for thr, rew in TRACK:
        rstr = _reward_str(rew)
        if thr in claimed:
            L.append(f"✅ {thr} очк. — {rstr}")
        elif pts >= thr:
            L.append(f"🎁 {thr} очк. — {rstr} _(доступно к получению)_")
        else:
            L.append(f"🔒 {thr} очк. — {rstr}")
    return "\n".join(L)
