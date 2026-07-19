# -*- coding: utf-8 -*-
"""
Аналитика воронки регистрации + deep-link атрибуция (Этап 7.1).

Модуль чистый (без Telegram, без прямых обращений к asyncpg): события копятся
в буфере в памяти функцией track()/track_once(), а фактическую запись в БД
делает вызывающий — flush() отдаёт накопленный батч и чистит буфер, либо
flush_to_db() (используя инъекцию set_db(), см. паттерн ai/memory.py) сама
пишет батч через engine.db.Database.add_events_batch и чистит буфер. Кто
именно вызывает flush — решает bot/main.py (snapshot_worker, тем же тактом,
что и флаш «грязных» персонажей — см. Задачу 4 Этапа 7.1).

Без пула (pool=None / set_db не вызван) — буфер всё равно копится в track(),
просто flush_to_db() не находит куда его записать: батч дренируется впустую
(dev-режим/тесты без Postgres) — метрики за это окно теряются, что ожидаемо.

Каталог EVENTS — единственный источник истины о допустимых именах событий.
track() неизвестного события не падает — пишет warning в лог и молча
игнорирует (телеметрия не должна ронять игру).
"""
import re
import time
from typing import Dict, List, Optional

from . import log as _elog

_logger = _elog.get("engine.analytics")

# ───────── каталог событий воронки (единственный источник истины) ─────────
EVENTS = {
    # воронка регистрации
    "registration_started",
    "race_selected",
    "class_selected",
    "character_created",
    # первые шаги (дедуп через track_once + ch.flags)
    "first_move",
    "first_combat",
    "first_skill",
    "first_quest_accept",
    "first_quest_complete",
    # прогрессия/жизненный цикл
    "level_up",
    "death",
    # сессии (session_end НЕ эмитится — см. модуль bot/main.py:_mark_session,
    # конец сессии вычисляется постфактум по паузам между session_start в
    # scripts/funnel_report.py)
    "session_start",
    "session_end",
    # социалка
    "party_join",
    "guild_join",
    # push-уведомления
    "notification_sent",
    "notification_opened",
    # монетизация/лавка
    "shop_view",
    # зарезервированы под будущую монетизацию (Этап 7.2+): уже в каталоге,
    # чтобы track() не считал их «неизвестными», хотя пока нигде не эмитятся
    "payment_initiated",
    "payment_completed",
    "payment_refunded",
}

_ONCE_KEY = "analytics_once"   # ключ в ch.flags для дедупа track_once

_buffer: List[dict] = []
_db = None   # инъекция из bot/main.py (или тестов); None -> flush_to_db() дренирует впустую


def set_db(db) -> None:
    """Инъекция слоя БД (engine.db.Database) из bot/main.py после db.connect().
    None — flush_to_db() будет только дренировать буфер, ничего не записывая
    (fallback для dev-режима/тестов без Postgres)."""
    global _db
    _db = db


def _has_db() -> bool:
    return _db is not None and getattr(_db, "pool", None) is not None


def track(uid, event: str, props: Optional[dict] = None) -> None:
    """Положить событие в буфер в памяти. Неизвестное событие (не из EVENTS)
    в буфер НЕ попадает — пишем warning и тихо выходим, телеметрия не должна
    ронять игровой поток."""
    if event not in EVENTS:
        # ВАЖНО: у log_err() второй позиционный параметр сам называется
        # `event` (короткий машинный ключ) — не передавать сюда одноимённый
        # kwarg, иначе TypeError «multiple values for argument 'event'».
        _elog.log_err(_logger, "analytics_unknown_event", bad_event=str(event), uid=uid)
        return
    _buffer.append({
        "uid": uid,
        "event": event,
        "props": dict(props or {}),
        "ts": time.time(),
    })


def track_once(ch, event: str, props: Optional[dict] = None) -> bool:
    """Трекнуть событие максимум ОДИН раз для персонажа (first_move,
    first_combat, first_skill, first_quest_accept, first_quest_complete —
    и любое другое по необходимости). Дедуп — список уже пройденных событий
    в ch.flags['analytics_once'] (переживает рестарт бота вместе с
    персонажем, в отличие от дедупа в памяти процесса).

    -> True, если событие трекнуто впервые сейчас; False — уже было раньше."""
    done = ch.flags.setdefault(_ONCE_KEY, [])
    if event in done:
        return False
    done.append(event)
    track(getattr(ch, "uid", None), event, props)
    return True


def flush() -> List[dict]:
    """Забрать весь накопленный буфер и очистить его. Чистая функция без I/O —
    ЧТО делать с батчем (писать в БД, логировать, отбросить) решает вызывающий.
    """
    global _buffer
    rows = _buffer
    _buffer = []
    return rows


async def flush_to_db() -> int:
    """Слить буфер в БД через инъекцию set_db() — удобный хелпер для
    snapshot_worker (Задача 4 Этапа 7.1: тот же такт, что и флаш персонажей).
    Буфер дренируется в ЛЮБОМ случае (даже без БД — dev-режим, метрики этого
    окна теряются). Возвращает число ЗАПИСАННЫХ в БД событий (0, если БД
    недоступна или буфер был пуст)."""
    rows = flush()
    if not rows:
        return 0
    if _has_db():
        await _db.add_events_batch(rows)
        return len(rows)
    return 0


# ───────── deep-link атрибуция: чистая функция без побочных эффектов ─────────
_REF_ARG_RE = re.compile(r"^ref_(\d+)$")
_SRC_ARG_RE = re.compile(r"^src_(.+)$")
_CAMPAIGN_UNSAFE_RE = re.compile(r"[^A-Za-z0-9_\-]")
_CAMPAIGN_MAX = 32


def _clean_campaign(raw: str) -> str:
    """Вычистить имя маркетинг-кампании до безопасного вида: только
    [A-Za-z0-9_-], обрезка до _CAMPAIGN_MAX символов. Уже, чем алфавит имён
    персонажей (engine/textsafe.py) — кампания технический ярлык, не UGC для
    показа, поэтому пробелы/юникод/markdown-спецсимволы просто вырезаются, а
    не экранируются."""
    return _CAMPAIGN_UNSAFE_RE.sub("", raw)[:_CAMPAIGN_MAX]


def source_from_start_arg(text) -> str:
    """Разобрать текст команды /start в источник трафика для attribution.

    Приоритет: ref_<uid> (рефералка, см. engine/referral.py:parse_start_arg)
    > src_<campaign> (маркетинг-ссылка) > organic (без аргумента или мусор).
    Telegram передаёт аргумент deep-link'а ОДНИМ токеном, так что оба формата
    совпасть в одном вызове не могут — порядок проверки здесь лишь для
    предсказуемости, а не для реального разрешения конфликтов.

    -> "ref:<uid>" | "src:<campaign>" | "organic"
    """
    if not text:
        return "organic"
    parts = str(text).strip().split(maxsplit=1)
    if len(parts) < 2:
        return "organic"
    arg = parts[1].strip()
    m_ref = _REF_ARG_RE.match(arg)
    if m_ref:
        return f"ref:{m_ref.group(1)}"
    m_src = _SRC_ARG_RE.match(arg)
    if m_src:
        campaign = _clean_campaign(m_src.group(1))
        return f"src:{campaign}" if campaign else "organic"
    return "organic"
