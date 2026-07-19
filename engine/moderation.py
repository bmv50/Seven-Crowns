# -*- coding: utf-8 -*-
"""
Модерация: баны, муты и чат-rate-limit (Этап 7.2).

Мотив закрытой беты: токсичного игрока нужно останавливать БЕЗ ручной правки БД,
а у каждого модер-действия должен быть журнал (audit_log). Модуль живёт в engine/
и НЕ зависит от aiogram — состояние решается синхронными функциями, которые
безопасно звать в горячем пути обработчиков (on_text/on_cb), а запись/аудит —
асинхронно, через инъекцию БД (set_db, как ai/memory и analytics).

Модель:
  • Источник истины по банам/мутам — таблица moderation в PostgreSQL, но в
    рантайме читаем из кэша в памяти (_state): гейты дёргаются на каждое действие
    игрока, ходить в БД накладно. ban/mute/… мутируют кэш СРАЗУ и персистят в БД
    (idempotent upsert) + пишут audit_log. При старте load() поднимает кэш из БД.
  • Без пула (dev, pool=None) кэш — единственное хранилище (get/set в db.py тихо
    деградируют): игра работает, состояние живёт до перезапуска.
  • Чат-rate-limit — чистое скользящее окно в памяти (chat_allowed): ≤ CHAT_MAX
    сообщений за CHAT_WINDOW секунд на игрока. Спам-защита рантайма, в БД не пишем.

Экспорт:
  set_db(db) / load() / reset()
  is_banned(uid) / is_muted(uid, now) / muted_until(uid)          — синхронные гейты
  ban / unban / mute / unmute                                     — async, с аудитом
  chat_allowed(uid, now)                                          — окно спама
"""
import time
from collections import deque

# ───────── настройки чат-лимита ─────────
CHAT_MAX = 5          # не более стольких сообщений...
CHAT_WINDOW = 10.0    # ...за столько секунд (скользящее окно)

# инъекция БД (как в ai/memory.set_db) — pool=None у db → тихая деградация в память
_db = None

# кэш модер-состояния: uid -> {"banned","muted_until","reason","by","updated"}
_state: dict = {}
# окна анти-спама: uid -> deque[unix-времена принятых сообщений]
_chat: dict = {}


def set_db(db):
    """Внедрить объект БД (Database). None/pool=None → работа только в памяти."""
    global _db
    _db = db


def reset():
    """Сбросить состояние и окна (для тестов/перезапуска)."""
    _state.clear()
    _chat.clear()


def _blank() -> dict:
    return {"banned": False, "muted_until": 0.0, "reason": "", "by": 0, "updated": 0.0}


async def load():
    """Поднять кэш банов/мутов из БД при старте. Без пула — кэш остаётся пустым."""
    if _db is None:
        return
    try:
        rows = await _db.load_moderation()
    except Exception:
        return
    for r in rows or []:
        uid = int(r["uid"])
        _state[uid] = {
            "banned": bool(r.get("banned")),
            "muted_until": float(r.get("muted_until") or 0.0),
            "reason": r.get("reason") or "",
            "by": int(r.get("by_admin") or 0),
            "updated": float(r.get("updated") or 0.0),
        }


# ───────── синхронные гейты (горячий путь) ─────────
def record(uid: int) -> dict:
    """Вернуть (создав при нужде) запись состояния игрока."""
    r = _state.get(uid)
    if r is None:
        r = _blank()
        _state[uid] = r
    return r


def is_banned(uid: int) -> bool:
    r = _state.get(uid)
    return bool(r and r.get("banned"))


def muted_until(uid: int) -> float:
    r = _state.get(uid)
    return float(r.get("muted_until", 0.0)) if r else 0.0


def is_muted(uid: int, now: float = None) -> bool:
    now = time.time() if now is None else now
    return muted_until(uid) > now


# ───────── мутирующие действия (async, с аудитом) ─────────
async def _persist(uid: int):
    """Записать текущее состояние игрока в БД (idempotent upsert). Без пула — no-op."""
    if _db is None:
        return
    r = _state.get(uid) or _blank()
    try:
        await _db.set_moderation(uid, bool(r["banned"]), float(r["muted_until"]),
                                 r.get("reason") or "", int(r.get("by") or 0))
    except Exception:
        pass


async def _audit(action: str, uid: int, details: dict):
    if _db is None:
        return
    try:
        await _db.add_audit(uid, action, details)
    except Exception:
        pass


async def ban(uid: int, reason: str = "", by: int = 0):
    """Забанить игрока (полный запрет). Мутирует кэш, персистит, пишет аудит."""
    r = record(uid)
    r["banned"] = True
    r["reason"] = reason or ""
    r["by"] = int(by)
    r["updated"] = time.time()
    await _persist(uid)
    await _audit("mod_ban", uid, {"reason": reason or "", "by": int(by)})


async def unban(uid: int, by: int = 0):
    """Снять бан."""
    r = record(uid)
    r["banned"] = False
    r["by"] = int(by)
    r["updated"] = time.time()
    await _persist(uid)
    await _audit("mod_unban", uid, {"by": int(by)})


async def mute(uid: int, minutes: float, reason: str = "", by: int = 0, now: float = None):
    """Замутить игрока на minutes минут (запрет писать в чат)."""
    now = time.time() if now is None else now
    r = record(uid)
    r["muted_until"] = now + float(minutes) * 60.0
    r["reason"] = reason or ""
    r["by"] = int(by)
    r["updated"] = now
    await _persist(uid)
    await _audit("mod_mute", uid,
                 {"minutes": float(minutes), "reason": reason or "", "by": int(by)})


async def unmute(uid: int, by: int = 0):
    """Снять мут досрочно."""
    r = record(uid)
    r["muted_until"] = 0.0
    r["by"] = int(by)
    r["updated"] = time.time()
    await _persist(uid)
    await _audit("mod_unmute", uid, {"by": int(by)})


# ───────── чат-rate-limit (чистое окно в памяти) ─────────
def chat_allowed(uid: int, now: float = None) -> bool:
    """Разрешить сообщение, если за последние CHAT_WINDOW секунд их было < CHAT_MAX.

    Скользящее окно: подрезаем устаревшие метки, при разрешении фиксируем now.
    Чистая (без БД) защита рантайма от флуда в чате комнаты/гильдии/группы."""
    now = time.time() if now is None else now
    dq = _chat.get(uid)
    if dq is None:
        dq = deque()
        _chat[uid] = dq
    horizon = now - CHAT_WINDOW
    while dq and dq[0] <= horizon:
        dq.popleft()
    if len(dq) >= CHAT_MAX:
        return False
    dq.append(now)
    return True
