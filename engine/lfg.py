# -*- coding: utf-8 -*-
"""
Доска «ищу группу» и час сбора.

Зачем модуль вообще нужен. Мультиплеер заявлен главным хуком, но мир — 291
комната (97 рукотворных + дикие), а онлайн на бете — единицы. Вероятность
случайно встретить живого игрока близка к нулю, поэтому ВСЕ социальные системы
(пати, гильдии, аукцион, арена, кооп-боссы) стоят пустыми: им нужна критическая
масса в ОДНОЙ точке, а её нечем создать. До этого единственным способом узнать,
кто онлайн, была текстовая команда «кто» — без кнопки, без расстояния и без
возможности к человеку дойти.

Здесь два простых механизма против этого:

  1. ДОСКА ПОИСКА — игрок встаёт в поиск, видит других в своём окне уровней и
     получает готовый маршрут до каждого (BFS по комнатам, engine/nav.py).
     Запись живёт TTL и протухает сама — «мёртвых» объявлений не копится.
  2. ЧАС СБОРА — раз в сутки в GATHER_HOUR по ЛОКАЛЬНОМУ времени игрока всем
     уходит пуш «через час — сбор в деревне». Это искусственно сводит онлайн в
     одну точку в предсказуемое время; без такого якоря низкий CCU не
     самоорганизуется никогда.

Состояние — в памяти процесса (как мир и пати): доска эфемерна по смыслу,
переживать рестарт ей незачем. Модуль без Telegram и без БД.
"""
import time
from typing import Dict, List, Optional

from .content import WORLD
from . import nav
from . import notify

# ── доска поиска ──
TTL = 1800            # сколько живёт запись, сек (30 мин)
LEVEL_WINDOW = 5      # с кем реально играть вместе: ±5 уровней
MAX_SHOWN = 8         # не раздувать экран
NOTE_MAX = 60         # длина комментария к записи

# ── час сбора ──
GATHER_ROOM = "village"   # общий стартовый хаб: самая плотная комната сезона
GATHER_HOUR = 20          # локальный час игрока, когда уходит пуш

# uid -> {"uid","name","cls","level","room","note","at"}
_BOARD: Dict[int, dict] = {}


def _now(now: Optional[float] = None) -> float:
    return time.time() if now is None else now


def purge(now: float = None) -> int:
    """Убрать протухшие записи. Возвращает число удалённых."""
    now = _now(now)
    dead = [uid for uid, e in _BOARD.items() if now - e["at"] > TTL]
    for uid in dead:
        _BOARD.pop(uid, None)
    return len(dead)


def clear() -> None:
    _BOARD.clear()


def join(ch, note: str = "", now: float = None) -> dict:
    """Встать в поиск (или обновить свою запись — таймер начинается заново)."""
    now = _now(now)
    purge(now)
    entry = {
        "uid": ch.uid,
        "name": getattr(ch, "name", ""),
        "cls": getattr(ch, "cls", ""),
        "level": int(getattr(ch, "level", 1) or 1),
        "room": getattr(ch, "room", GATHER_ROOM),
        "note": (note or "").strip()[:NOTE_MAX],
        "at": now,
    }
    _BOARD[ch.uid] = entry
    return entry


def leave(uid: int) -> bool:
    return _BOARD.pop(uid, None) is not None


def is_listed(uid: int, now: float = None) -> bool:
    purge(now)
    return uid in _BOARD


def entries(now: float = None) -> List[dict]:
    """Все живые записи, свежие сверху."""
    purge(now)
    return sorted(_BOARD.values(), key=lambda e: -e["at"])


def touch_room(ch) -> None:
    """Обновить комнату в своей записи (игрок ходит, пока стоит в поиске)."""
    e = _BOARD.get(getattr(ch, "uid", None))
    if e is not None:
        e["room"] = getattr(ch, "room", e["room"])


def route(from_room: str, to_room: str) -> Optional[List[str]]:
    """Маршрут (список направлений) между комнатами. [] — уже здесь, None — нет пути."""
    if from_room not in WORLD or to_room not in WORLD:
        return None
    return nav.bfs_path(from_room, lambda r: r == to_room)


def board_for(ch, now: float = None) -> List[dict]:
    """Чужие записи в окне уровней игрока, ближние сверху.

    К каждой добавлены "route" (список направлений) и "steps" (длина пути,
    None — пути нет). Сортировка по расстоянию: смысл доски в том, чтобы
    дойти, а не просто посмотреть на список.
    """
    now = _now(now)
    me = int(getattr(ch, "level", 1) or 1)
    out = []
    for e in entries(now):
        if e["uid"] == getattr(ch, "uid", None):
            continue
        if abs(e["level"] - me) > LEVEL_WINDOW:
            continue
        r = route(getattr(ch, "room", GATHER_ROOM), e["room"])
        out.append({**e, "route": r, "steps": None if r is None else len(r)})
    out.sort(key=lambda e: (e["steps"] is None, e["steps"] or 0, e["uid"]))
    return out[:MAX_SHOWN]


# ───────────────────────── час сбора ─────────────────────────
def gather_text() -> str:
    room = WORLD.get(GATHER_ROOM, {}).get("name", GATHER_ROOM)
    return (f"👥 *Час сбора* — через час, в «{room}». "
            f"Собираемся отрядом: боссы и данжи проходятся вместе.")


def gather_due(ch, now: float = None) -> bool:
    """Пора ли слать этому игроку приглашение на сбор (раз в сутки, локально).

    Час считается по ЧАСОВОМУ ПОЯСУ ИГРОКА (notify._hour): «в 20:00» должно
    означать его восемь вечера, а не серверные.
    """
    now = _now(now)
    if notify._hour(now, ch) != GATHER_HOUR:
        return False
    return ch.flags.get("gather_day") != notify._today(now)


def tick_gather(chars: dict, now: float = None) -> int:
    """Разослать приглашения на час сбора. Возвращает число отправленных."""
    if not notify.ENABLED:
        return 0
    now = _now(now)
    sent = 0
    for ch in list((chars or {}).values()):
        try:
            if not gather_due(ch, now):
                continue
            ch.flags["gather_day"] = notify._today(now)
            notify.emit(ch.uid, "gather", gather_text())
            sent += 1
        except Exception:            # noqa: BLE001
            continue                 # приглашение на сбор не должно ронять тик
    return sent
