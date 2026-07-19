# -*- coding: utf-8 -*-
"""
Журнал вызовов LLM (Этап 8): каждое РЕАЛЬНОЕ обращение к модели фиксируется
одной записью — провайдер, модель, tier, латентность, токены, оценка стоимости,
исход и контекст вызова + версия промпта. Чистый модуль (без Telegram, без
прямых asyncpg): record() копит в буфере в памяти, а фактическую запись батчем
в БД (таблица llm_log) делает snapshot_worker тем же тактом, что и аналитику
(flush_to_db через инъекцию set_db, паттерн engine/analytics.py).

Зачем это нужно (регламент беты):
  1. стоимость на DAU становится ИЗМЕРИМОЙ — сумма cost_est за день / число
     активных игроков;
  2. дневной расход из этого журнала питает HARD-бюджет (ai/cost.py:BUDGET_GUARD),
     который при превышении АВАРИЙНО отключает LLM (provider.enabled() → False);
  3. по outcome (ok|error|timeout|invalid) и context (npc|god|errand|epic) видно,
     где LLM буксует, а по version — какой промпт это породил.

Оценка стоимости — по прайсу модели PRICE_PER_1K (USD за 1000 токенов). Для
deepseek-chat дефолты переопределяются через env DEEPSEEK_PRICE_IN/OUT. ВАЖНО:
дефолтные цены ориентировочные — УТОЧНИТЬ актуальный прайс DeepSeek перед
публичной бетой.

Без пула (set_db не вызван / pool=None) — буфер копится и дренируется впустую
(dev/тесты без Postgres), игра работает как раньше.
"""
import os
import time
from typing import List, Optional

from engine import log as _elog

_logger = _elog.get("ai.llmlog")

# ───────── прайс модели (USD за 1000 токенов) ─────────
# PRICE_PER_1K — единственный источник дефолтов. Для deepseek-chat значения
# переопределяются переменными окружения DEEPSEEK_PRICE_IN / DEEPSEEK_PRICE_OUT
# (УТОЧНИТЬ перед продом — цифры ниже ориентировочные).
PRICE_PER_1K = {
    "deepseek-chat": (0.00014, 0.00028),   # (вход, выход) — УТОЧНИТЬ (env DEEPSEEK_PRICE_IN/OUT)
}

# допустимые значения (валидация «мягкая»: неизвестное чинится к дефолту, не падаем)
ALLOWED_OUTCOMES = {"ok", "error", "timeout", "invalid"}
ALLOWED_CONTEXTS = {"npc", "god", "errand", "epic"}

_buffer: List[dict] = []
_db = None   # инъекция из bot/main.py (или тестов); None -> flush_to_db() дренирует впустую


def set_db(db) -> None:
    """Инъекция слоя БД (engine.db.Database) из bot/main.py после db.connect().
    None — flush_to_db() только дренирует буфер (dev-режим/тесты без Postgres)."""
    global _db
    _db = db


def _has_db() -> bool:
    return _db is not None and getattr(_db, "pool", None) is not None


def _price_env(name: str, default: float) -> float:
    try:
        return float((os.environ.get(name) or "").strip() or default)
    except (ValueError, TypeError):
        return default


def price_for(model: str) -> tuple:
    """Прайс (in, out) USD за 1К токенов для модели. deepseek* — из env
    (DEEPSEEK_PRICE_IN/OUT) с дефолтом из PRICE_PER_1K; иначе — из PRICE_PER_1K;
    неизвестная модель -> (0.0, 0.0) (стоимость не оцениваем, но не падаем)."""
    if model and model.startswith("deepseek"):
        din, dout = PRICE_PER_1K.get("deepseek-chat", (0.0, 0.0))
        return (_price_env("DEEPSEEK_PRICE_IN", din),
                _price_env("DEEPSEEK_PRICE_OUT", dout))
    return PRICE_PER_1K.get(model, (0.0, 0.0))


def estimate_cost(model: str, tokens_in, tokens_out) -> float:
    """Оценка стоимости вызова по прайсу модели (USD)."""
    p_in, p_out = price_for(model)
    ti = max(0, int(tokens_in or 0))
    to = max(0, int(tokens_out or 0))
    return (ti / 1000.0) * p_in + (to / 1000.0) * p_out


def record(provider: str, model: str, tier: str, latency_ms, tokens_in, tokens_out,
           outcome: str, context: str, version: str = "",
           cost_est: Optional[float] = None, ts: float = None) -> dict:
    """Зафиксировать один вызов LLM в буфер в памяти.

    cost_est=None -> посчитать по прайсу модели (estimate_cost). Неизвестный
    outcome/context не роняет запись (чинится к безопасному значению). Возвращает
    записанную строку (удобно для тестов и для инкремента дневного расхода)."""
    if outcome not in ALLOWED_OUTCOMES:
        outcome = "error"
    ti = max(0, int(tokens_in or 0))
    to = max(0, int(tokens_out or 0))
    if cost_est is None:
        cost_est = estimate_cost(model, ti, to)
    row = {
        "provider": str(provider or "?"),
        "model": str(model or "?"),
        "tier": str(tier or ""),
        "latency_ms": int(latency_ms or 0),
        "tokens_in": ti,
        "tokens_out": to,
        "cost_est": float(cost_est),
        "outcome": outcome,
        "context": str(context or ""),
        "version": str(version or ""),
        "ts": ts if ts is not None else time.time(),
    }
    _buffer.append(row)
    return row


def buffered_cost() -> float:
    """Суммарная оценка стоимости накопленных (ещё НЕ слитых) записей — читается
    ПЕРЕД flush(), чтобы проинкрементить дневной расход бюджета (ai/cost.py)."""
    return sum(float(r.get("cost_est") or 0.0) for r in _buffer)


def buffered_count() -> int:
    return len(_buffer)


def flush() -> List[dict]:
    """Забрать весь буфер и очистить его. Чистая функция без I/O — ЧТО делать с
    батчем (писать в БД, инкрементить бюджет) решает вызывающий (snapshot_worker)."""
    global _buffer
    rows = _buffer
    _buffer = []
    return rows


async def flush_to_db() -> int:
    """Слить буфер в БД (llm_log) через инъекцию set_db(). Буфер дренируется в
    ЛЮБОМ случае (даже без БД — метрики окна теряются, dev-режим). Возвращает
    число записанных строк (0 без БД / при пустом буфере)."""
    rows = flush()
    if not rows:
        return 0
    if _has_db():
        await _db.add_llm_batch(rows)
        return len(rows)
    return 0
