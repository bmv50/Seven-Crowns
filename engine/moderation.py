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
    игрока, ходить в БД накладно. При старте load() поднимает кэш из БД.
  • Без пула (dev, pool=None) кэш — единственное хранилище (db.py-методы тихо
    деградируют в no-op): игра работает, состояние живёт до перезапуска — это
    ОСОЗНАННАЯ деградация, не путать с fail-open ниже.
  • FAIL-CLOSED (Аудит-2а.2): раньше ban/unban/mute/unmute мутировали кэш
    СРАЗУ, а персист (set_moderation) и аудит (add_audit) звались по отдельности
    и оба глотали исключения (`except Exception: pass`) — при сбое БД кэш уже
    считал действие применённым, хотя в БД могло не остаться следа (рестарт
    тихо «прощал» бан). Теперь: персист+аудит — ОДНОЙ транзакцией
    (db.set_moderation_with_audit), а кэш меняется ТОЛЬКО ПОСЛЕ успешного
    commit; ошибка БД не проглатывается — исключение уходит наружу
    вызывающему (bot/main.py решает, как ответить игроку/админу).
  • Чат-rate-limit — чистое скользящее окно в памяти (chat_allowed): ≤ CHAT_MAX
    сообщений за CHAT_WINDOW секунд на игрока. Спам-защита рантайма, в БД не пишем.

Экспорт:
  set_db(db) / load() / reset()
  is_banned(uid) / is_muted(uid, now) / muted_until(uid)          — синхронные гейты
  ban / unban / mute / unmute                                     — async, с аудитом,
                                                                     fail-closed
  chat_allowed(uid, now)                                          — окно спама
"""
import time
from collections import deque

from . import log as _elog

_log = _elog.get("engine.moderation")

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
    """Поднять кэш банов/мутов из БД при старте. Без пула — кэш остаётся
    пустым (ожидаемо: dev без БД работает только в памяти).

    С пулом ошибка ЗАГРУЗКИ теперь НЕ проглатывается (Аудит-2а.2): подняться
    с молча пустым кэшем банов значит временно снять все ограничения с
    забаненных игроков. Решение «упасть или продолжить» — не дело engine/
    (тут нет понятия PROD) — исключение уходит наружу вызывающему
    (bot/main.py: PROD → критический лог + sys.exit(1); dev → warning и
    продолжить, см. вызов _mod.load() в main())."""
    if _db is None:
        return
    rows = await _db.load_moderation()
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


# ───────── мутирующие действия (async, с аудитом, fail-closed) ─────────
def _snapshot(uid: int) -> dict:
    """Копия текущей записи игрока (или пустой бланк, если её ещё нет) — НЕ
    мутирует _state. Действия ниже считают новое состояние поверх этой копии
    и применяют его к кэшу только после успешного commit в БД."""
    r = _state.get(uid)
    return dict(r) if r else _blank()


async def _apply(uid: int, new: dict, action: str, details: dict):
    """Персист+аудит ОДНОЙ транзакцией (db.set_moderation_with_audit), кэш —
    ТОЛЬКО ПОСЛЕ успеха (Аудит-2а.2, fail-closed).

    Раньше запись в БД (set_moderation) и аудит (add_audit) звались отдельно
    ПОСЛЕ мутации кэша, и оба глотали исключения — кэш «врал», что действие
    применено, даже если в БД не осталось следа. Теперь: кэш собирается в
    `new` ЗАРАНЕЕ (без побочных эффектов), затем одна транзакция; при ошибке
    БД кэш НЕ трогаем (никакого отката не нужно — мы ничего не меняли) и
    исключение уходит НАРУЖУ вызывающему — тот решает, как ответить игроку.

    Без пула (_db is None или db.pool is None) — set_moderation_with_audit
    сама делает no-op без исключения (осознанная деградация в память, dev)."""
    if _db is not None:
        try:
            await _db.set_moderation_with_audit(
                uid, bool(new["banned"]), float(new["muted_until"]),
                new.get("reason") or "", int(new.get("by") or 0), action, details)
        except Exception as e:
            _elog.log_err(_log, "moderation_persist_failed", e, uid=uid, action=action)
            raise
    _state[uid] = new


async def ban(uid: int, reason: str = "", by: int = 0):
    """Забанить игрока (полный запрет). Fail-closed: см. _apply."""
    new = _snapshot(uid)
    new.update(banned=True, reason=reason or "", by=int(by), updated=time.time())
    await _apply(uid, new, "mod_ban", {"reason": reason or "", "by": int(by)})


async def unban(uid: int, by: int = 0):
    """Снять бан. Fail-closed: см. _apply."""
    new = _snapshot(uid)
    new.update(banned=False, by=int(by), updated=time.time())
    await _apply(uid, new, "mod_unban", {"by": int(by)})


async def mute(uid: int, minutes: float, reason: str = "", by: int = 0, now: float = None):
    """Замутить игрока на minutes минут (запрет писать в чат). Fail-closed: см. _apply."""
    now = time.time() if now is None else now
    new = _snapshot(uid)
    new.update(muted_until=now + float(minutes) * 60.0, reason=reason or "",
               by=int(by), updated=now)
    await _apply(uid, new, "mod_mute",
                {"minutes": float(minutes), "reason": reason or "", "by": int(by)})


async def unmute(uid: int, by: int = 0):
    """Снять мут досрочно. Fail-closed: см. _apply."""
    new = _snapshot(uid)
    new.update(muted_until=0.0, by=int(by), updated=time.time())
    await _apply(uid, new, "mod_unmute", {"by": int(by)})


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
