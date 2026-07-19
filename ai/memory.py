# -*- coding: utf-8 -*-
"""
Долгая память ИИ-NPC о конкретном игроке (Фаза 3).

Архитектура: воспоминания копятся в Postgres (engine.db, таблица
npc_memories), при выборке для промпта ранжируются функцией rank() —
свежесть + лексическая похожесть на текущую реплику игрока. Когда БД
недоступна (pool=None, локальная разработка без Postgres) — тихая
деградация на fallback-список в ch.flags["npc_mem2"] (до 5 последних
записей), а легаси-строка ch.flags["npc_mem"] (старая память «одна строка
≤300 симв.») читается как самая старая запись — обратная совместимость,
старый формат не трогаем и не ломаем.

Инъекция БД: bot/main.py вызывает set_db(db) после db.connect(), сам модуль
БД не создаёт и aiogram не импортирует (ai/ остаётся чистым от Telegram).

Готовность к pgvector: rank() — единственная точка, которую в будущем
заменит векторный поиск (cosine similarity по эмбеддингам вместо
лексического Jaccard); сигнатуры store()/retrieve() менять не придётся.
"""
import time
from typing import List, Optional, Tuple

from . import cost

_db = None  # инъекция из bot/main.py (или тестов); None -> только fallback в ch.flags

# ───────── настройки ранжирования и лимитов (константы; см. ai/cost.py для
# примера вынесения в env, здесь пока не требуется) ─────────
RECENCY_HALF_LIFE_DAYS = 7.0   # полураспад свежести воспоминания
W_RECENCY = 0.6
W_LEX = 0.4
MAX_BLOCK_CHARS = 350           # совокупный размер блока памяти для промпта
DB_TEXT_MAX = 200               # лимит одной записи при записи в БД
FALLBACK_ITEM_MAX = 120         # лимит записи в fallback-списке (ch.flags)
FALLBACK_LIST_MAX = 5           # сколько последних записей держим без БД


def set_db(db):
    """Инъекция слоя БД (engine.db.Database) из bot/main.py. None — работать
    только на fallback в ch.flags (например, в тестах или без Postgres)."""
    global _db
    _db = db


def _has_db() -> bool:
    return _db is not None and getattr(_db, "pool", None) is not None


def _lex_sim(a: str, b: str) -> float:
    """Лексическая похожесть (Jaccard по словам) — переиспользуем ai/cost.py,
    ту же меру, что и в SemanticCache. Единственное место, которое заменит
    будущий pgvector."""
    return cost.jaccard(cost._wordset(a), cost._wordset(b))


def _recency(ts: float, now: float) -> float:
    """Экспоненциальное затухание свежести, полураспад RECENCY_HALF_LIFE_DAYS."""
    if RECENCY_HALF_LIFE_DAYS <= 0:
        return 0.0
    age_days = max(0.0, (now - ts) / 86400.0)
    return 0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS)


def rank(records: List[Tuple[float, str]], query: Optional[str], now: float,
         k: int = 3) -> List[str]:
    """ЧИСТАЯ синхронная функция ранжирования воспоминаний (тестируемая
    отдельно от БД и от asyncio).

    records — [(ts, text), ...] в любом порядке.
    score = 0.6×recency (эксп. затухание, полураспад 7 дней)
          + 0.4×lex_sim(query, text) (Jaccard по словам, ai/cost.py);
    query=None -> score = чистая свежесть (без лексической части).

    Возвращает top-k текстов, самые релевантные первыми. При равенстве
    очков — более свежие впереди, затем исходный порядок записи (стабильность).

    Это единственная функция, которую заменит будущий pgvector (поиск по
    embedding вместо Jaccard) — retrieve() и её вызывающие не изменятся.
    """
    scored = []
    for i, (ts, text) in enumerate(records):
        rec = _recency(ts, now)
        if query:
            score = W_RECENCY * rec + W_LEX * _lex_sim(query, text)
        else:
            score = rec
        scored.append((score, ts, i, text))
    scored.sort(key=lambda x: (-x[0], -x[1], x[2]))
    return [text for _score, _ts, _i, text in scored[:max(0, k)]]


def _fallback_records(ch, npc_id: str, now: float) -> List[Tuple[float, str]]:
    """Собрать записи без БД: fallback-список ch.flags['npc_mem2'][npc_id]
    (append-порядок: от старых к новым) + легаси-строка ch.flags['npc_mem']
    как самая старая запись, если она есть."""
    lst = ch.flags.get("npc_mem2", {}).get(npc_id, [])
    records: List[Tuple[float, str]] = []
    # искусственно разносим записи списка по времени (минута на запись), чтобы
    # у них была разная свежесть при ранжировании (сам список порядок хранит)
    base = now - len(lst) * 60.0
    for i, text in enumerate(lst):
        records.append((base + i * 60.0, text))
    legacy = ch.flags.get("npc_mem", {}).get(npc_id)
    if legacy:
        oldest_ts = (records[0][0] - 3600.0) if records else (now - 3600.0)
        records.insert(0, (oldest_ts, legacy))
    return records


def _fit_block(texts: List[str], limit: int = MAX_BLOCK_CHARS) -> List[str]:
    """Обрезать список текстов так, чтобы суммарный размер блока памяти для
    промпта не превышал limit символов (с учётом маркеров списка)."""
    out: List[str] = []
    total = 0
    for t in texts:
        sep = 2 if out else 0   # запас на маркер «— »/перенос строки
        room = limit - total - sep
        if room <= 0:
            break
        if len(t) > room:
            t = t[:room].rstrip()
            if t:
                out.append(t)
            break
        out.append(t)
        total += len(t) + sep
    return out


async def store(ch, npc_id: str, text: str):
    """Записать новое воспоминание: в БД (если доступна) и ВСЕГДА в fallback
    ch.flags['npc_mem2'] (офлайн-резерв / разработка без Postgres). Не трогает
    легаси-строку ch.flags['npc_mem'] — её по-прежнему пишет ai/npc_ai.py."""
    text = (text or "").strip()
    if not text:
        return
    text = text[:DB_TEXT_MAX]
    uid = getattr(ch, "uid", 0)

    if _has_db():
        try:
            await _db.add_npc_memory(uid, npc_id, text)
        except Exception:
            pass   # запись долгой памяти не должна ронять диалог

    lst = ch.flags.setdefault("npc_mem2", {}).setdefault(npc_id, [])
    lst.append(text[:FALLBACK_ITEM_MAX])
    if len(lst) > FALLBACK_LIST_MAX:
        del lst[:len(lst) - FALLBACK_LIST_MAX]


async def retrieve(ch, npc_id: str, query: Optional[str], k: int = 3,
                    now: float = None) -> List[str]:
    """Достать до k наиболее релевантных воспоминаний NPC об игроке для
    промпта. Источник — БД (get_npc_memories, до 20 последних) либо
    fallback-список + легаси-строка. Суммарный размер результата ограничен
    MAX_BLOCK_CHARS."""
    now = now if now is not None else time.time()
    uid = getattr(ch, "uid", 0)
    records: List[Tuple[float, str]] = []

    if _has_db():
        try:
            records = list(await _db.get_npc_memories(uid, npc_id, limit=20))
        except Exception:
            records = []

    if not records:
        records = _fallback_records(ch, npc_id, now)

    if not records:
        return []

    top = rank(records, query, now, k=k)
    return _fit_block(top)
