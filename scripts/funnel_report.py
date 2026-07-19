#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CLI-отчёт по воронке регистрации, D1/D7-ретеншену и топ-источникам трафика
(Этап 7.1: engine/analytics.py + engine/db.py: analytics_events/attribution).

Строит три среза:
  1. Воронка /start (registration_started) -> character_created ->
     first_quest_accept -> first_quest_complete — по уникальным uid, за
     последние --days суток.
  2. D1/D7-ретеншен: среди персонажей, СОЗДАННЫХ за окно --days, доля тех,
     у кого встречается событие session_start ровно в интервале [created+1д,
     created+2д) (D1) и [created+7д, created+8д) (D7). Это «вернулся именно
     на N-й день», а не «вернулся хоть когда-то после N дней» — стандартное,
     но более строгое определение когортного ретеншена.
  3. Топ источников трафика — attribution.first_source, вся история (не
     ограничено окном --days, т.к. это агрегат за всё время работы бота).

Источник данных — PostgreSQL (DATABASE_URL из окружения/.env, тот же .env,
что читает bot/main.py). Без доступной БД или без asyncpg — печатает понятное
сообщение и завершает с кодом 1 (не падает трейсбеком).

Запуск (из корня репозитория):
    python scripts/funnel_report.py               # окно 30 суток
    python scripts/funnel_report.py --days 7       # окно 7 суток

Живой прогон против боевой БД — на усмотрение владельца проекта; здесь только
сам инструмент.
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import asyncpg
except ImportError:
    asyncpg = None

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/mud")

# Шаги воронки в порядке отображения (ключ события каталога engine/analytics.py -> подпись).
_FUNNEL_STEPS = [
    ("registration_started", "Начали /start (без персонажа)"),
    ("character_created", "Создали персонажа"),
    ("first_quest_accept", "Взяли первый квест"),
    ("first_quest_complete", "Сдали первый квест"),
]

_DAY = 86400.0


async def _fetch_funnel(con, since_ts: float) -> dict:
    """Число УНИКАЛЬНЫХ uid на каждом шаге воронки за окно [since_ts; сейчас)."""
    out = {}
    for event, _label in _FUNNEL_STEPS:
        n = await con.fetchval(
            "SELECT count(DISTINCT uid) FROM analytics_events WHERE event=$1 AND created >= $2",
            event, since_ts)
        out[event] = int(n or 0)
    return out


async def _fetch_retention(con, since_ts: float):
    """D1/D7 по когорте character_created за окно since_ts..сейчас.
    -> (всего_создано, вернулись_D1, вернулись_D7)."""
    created_rows = await con.fetch(
        "SELECT uid, created FROM analytics_events "
        "WHERE event='character_created' AND created >= $1 AND uid IS NOT NULL",
        since_ts)
    total = len(created_rows)
    d1 = d7 = 0
    for row in created_rows:
        uid, c_ts = row["uid"], row["created"]
        has_d1 = await con.fetchval(
            "SELECT EXISTS(SELECT 1 FROM analytics_events WHERE uid=$1 AND event='session_start' "
            "AND created >= $2 AND created < $3)",
            uid, c_ts + _DAY, c_ts + 2 * _DAY)
        has_d7 = await con.fetchval(
            "SELECT EXISTS(SELECT 1 FROM analytics_events WHERE uid=$1 AND event='session_start' "
            "AND created >= $2 AND created < $3)",
            uid, c_ts + 7 * _DAY, c_ts + 8 * _DAY)
        d1 += bool(has_d1)
        d7 += bool(has_d7)
    return total, d1, d7


async def _fetch_top_sources(con, limit: int = 10):
    rows = await con.fetch(
        "SELECT first_source, count(*) AS n FROM attribution "
        "WHERE first_source IS NOT NULL GROUP BY first_source ORDER BY n DESC LIMIT $1",
        limit)
    return [(r["first_source"], int(r["n"])) for r in rows]


def _pct(part: int, total: int) -> str:
    return f"{(100.0 * part / total):.1f}%" if total else "—"


async def run(days: int) -> int:
    if asyncpg is None:
        print("❌ asyncpg не установлен — отчёт недоступен (pip install asyncpg).")
        return 1
    since_ts = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    try:
        con = await asyncpg.connect(DATABASE_URL)
    except Exception as e:
        print(f"❌ Нет подключения к PostgreSQL ({DATABASE_URL}): {e}")
        print("   Задайте корректный DATABASE_URL в окружении/.env и убедитесь, что БД поднята.")
        return 1
    try:
        funnel = await _fetch_funnel(con, since_ts)
        total, d1, d7 = await _fetch_retention(con, since_ts)
        sources = await _fetch_top_sources(con)
    finally:
        await con.close()

    print(f"═══════ ВОРОНКА РЕГИСТРАЦИИ (последние {days} дн.) ═══════")
    base = funnel.get(_FUNNEL_STEPS[0][0], 0)
    for event, label in _FUNNEL_STEPS:
        n = funnel.get(event, 0)
        print(f"  {label:<34} {n:>6}  ({_pct(n, base)} от старта воронки)")

    print(f"\n═══════ РЕТЕНШЕН (когорта character_created за {days} дн.) ═══════")
    print(f"  Создано персонажей: {total}")
    print(f"  D1 (сессия ровно на 1-е сутки): {d1} ({_pct(d1, total)})")
    print(f"  D7 (сессия ровно на 7-е сутки): {d7} ({_pct(d7, total)})")

    print(f"\n═══════ ТОП ИСТОЧНИКОВ ТРАФИКА (first_source, за всё время) ═══════")
    if not sources:
        print("  (нет данных в attribution)")
    for src, n in sources:
        print(f"  {src:<28} {n:>6}")

    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Отчёт по воронке регистрации, D1/D7 и источникам трафика (Этап 7.1)")
    ap.add_argument("--days", type=int, default=30,
                    help="глубина окна в сутках для воронки/ретеншена (по умолчанию 30)")
    args = ap.parse_args()
    return asyncio.run(run(args.days))


if __name__ == "__main__":
    sys.exit(main())
