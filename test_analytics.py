# -*- coding: utf-8 -*-
"""
Автономные тесты аналитики воронки + deep-link атрибуции (Этап 7.1):
engine/analytics.py (track/track_once/flush/flush_to_db/source_from_start_arg)
и engine/db.py (Database.add_events_batch/upsert_attribution).

Без Telegram и без реального PostgreSQL: для двух методов Database здесь своя
мок-СУБД (тот же приём, что в test_econ_tx.py/test_guild_tx.py) — pool.acquire()
отдаёт объект с execute()/executemany(), понимающий ТОЛЬКО те SQL-строки,
которые реально шлют add_events_batch/upsert_attribution (сопоставление по
подстроке).

Запуск из каталога проекта:
    python test_analytics.py
"""
import asyncio
import sys

from engine import analytics
from engine.db import Database
from engine.character import Character

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✅ {name}")
    else:
        _failed += 1
        print(f"  ❌ {name}")


def new_char(uid=1):
    ch = Character(uid=uid, name="t", cls="warrior", race="human")
    ch.init_vitals()
    return ch


def reset_analytics():
    """Слить буфер и снять инъекцию БД между сценариями, чтобы тесты не текли
    друг в друга (модуль хранит буфер/инъекцию на уровне модуля, не класса)."""
    analytics.flush()
    analytics.set_db(None)


# ───────── мок-СУБД для Database.add_events_batch/upsert_attribution ─────────
class _FakeCon:
    def __init__(self, store, events):
        self.store = store      # uid -> {'first_source','first_ts','last_source','last_ts'}
        self.events = events    # список (uid, event, props_json, ts)

    async def execute(self, sql, *args):
        if "INSERT INTO attribution" in sql:
            uid, source, ts = args
            row = self.store.get(uid)
            if row is None:
                self.store[uid] = {"first_source": source, "first_ts": ts,
                                    "last_source": source, "last_ts": ts}
            else:
                row["last_source"] = source
                row["last_ts"] = ts
        return "OK"

    async def executemany(self, sql, seq):
        if "INSERT INTO analytics_events" in sql:
            self.events.extend(list(seq))


class _AcquireCM:
    def __init__(self, con):
        self._con = con

    async def __aenter__(self):
        return self._con

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self):
        self.store = {}
        self.events = []

    def acquire(self):
        return _AcquireCM(_FakeCon(self.store, self.events))


def make_fake_db() -> Database:
    """Database с pool=_FakePool() — обходим __init__ (не нужен dsn/asyncpg)."""
    db = Database.__new__(Database)
    db.pool = _FakePool()
    return db


# ═══════════════════════ 1. Каталог EVENTS ═══════════════════════
print("\n[1] Каталог EVENTS")
_required = {
    "registration_started", "race_selected", "class_selected", "character_created",
    "first_move", "first_combat", "first_skill", "first_quest_accept",
    "first_quest_complete", "level_up", "death", "session_start", "session_end",
    "party_join", "guild_join", "notification_sent", "notification_opened",
    "shop_view", "payment_initiated", "payment_completed", "payment_refunded",
}
check("каталог содержит все обязательные события воронки", _required <= analytics.EVENTS)
check("каталог — множество строк (без дублей/мусора)",
      all(isinstance(e, str) and e for e in analytics.EVENTS))

# ═══════════════════════ 2. track() буферизует ═══════════════════════
print("\n[2] track(): валидное событие уходит в буфер")
reset_analytics()
analytics.track(111, "shop_view", {"vendor": "кузня"})
rows = analytics.flush()
check("после track() -> flush() в батче 1 запись", len(rows) == 1)
check("uid сохранён как передан", rows[0]["uid"] == 111)
check("event сохранён как передан", rows[0]["event"] == "shop_view")
check("props скопированы (не тот же объект, но равны)", rows[0]["props"] == {"vendor": "кузня"})
check("ts — число (unix-время)", isinstance(rows[0]["ts"], float))
check("после flush() буфер пуст", analytics.flush() == [])

# ═══════════════════════ 3. неизвестное событие не падает ═══════════════════════
print("\n[3] track(): неизвестное событие не роняет процесс и не буферизуется")
reset_analytics()
try:
    analytics.track(1, "totally_unknown_event", {})
    _raised = False
except Exception:
    _raised = True
check("track() неизвестного события не бросает исключение", not _raised)
check("неизвестное событие НЕ попало в буфер", analytics.flush() == [])

# ═══════════════════════ 4. track_once дедупит ═══════════════════════
print("\n[4] track_once(): дедуп first_* по ch.flags")
reset_analytics()
ch = new_char()
first = analytics.track_once(ch, "first_move")
second = analytics.track_once(ch, "first_move")
rows = analytics.flush()
check("первый track_once() -> True (трекнуто)", first is True)
check("повторный track_once() того же события -> False", second is False)
check("в буфере ровно 1 запись (второй вызов не задвоил)", len(rows) == 1)
check("флаг дедупа записан в ch.flags", "first_move" in ch.flags.get("analytics_once", []))

print("\n[5] track_once(): разные события на одном персонаже независимы")
reset_analytics()
ch = new_char()
analytics.track_once(ch, "first_move")
analytics.track_once(ch, "first_combat")
rows = analytics.flush()
check("first_move и first_combat затрекались оба (2 разных события)",
      {r["event"] for r in rows} == {"first_move", "first_combat"})

# ═══════════════════════ 6. flush_to_db() без БД ═══════════════════════
print("\n[6] flush_to_db(): без инъекции set_db буфер дренируется впустую")
reset_analytics()
analytics.track(1, "session_start", {})
analytics.track(1, "level_up", {"level": 2})
written = asyncio.run(analytics.flush_to_db())
check("без БД flush_to_db() вернул 0 записанных (dev-режим)", written == 0)
check("буфер после flush_to_db() всё равно пуст (дренирован)", analytics.flush() == [])

# ═══════════════════════ 7. source_from_start_arg ═══════════════════════
print("\n[7] source_from_start_arg(): рефералка, кампания, organic")
check('"/start ref_123" -> "ref:123"', analytics.source_from_start_arg("/start ref_123") == "ref:123")
check('"/start src_tiktok" -> "src:tiktok"',
      analytics.source_from_start_arg("/start src_tiktok") == "src:tiktok")
check('"/start" (без аргумента) -> "organic"', analytics.source_from_start_arg("/start") == "organic")
check("None -> \"organic\"", analytics.source_from_start_arg(None) == "organic")
check('"" -> "organic"', analytics.source_from_start_arg("") == "organic")
check('"/start ref_abc" (нечисловой uid) -> "organic"',
      analytics.source_from_start_arg("/start ref_abc") == "organic")
check('"/start ref_1 src_2" (два токена подряд) -> "organic"',
      analytics.source_from_start_arg("/start ref_1 src_2") == "organic")

print("\n[8] source_from_start_arg(): чистка мусора/инъекций в имени кампании")
_dirty = analytics.source_from_start_arg("/start src_<script>alert(1)")
check("результат начинается с 'src:'", _dirty.startswith("src:"))
check("опасные символы (<>()) вычищены из campaign", not any(c in _dirty for c in "<>()"))
_long = analytics.source_from_start_arg("/start src_" + "a" * 100)
check("имя кампании обрезано до ≤32 символов", len(_long.split(":", 1)[1]) <= 32)

# ═══════════════════════ 9. Database.upsert_attribution (мок-СУБД) ═══════════════════════
print("\n[9] Database.upsert_attribution(): first-only / last-always (мок-СУБД)")
db = make_fake_db()
asyncio.run(db.upsert_attribution(555, "ref:5"))
check("первый upsert -> first_source установлен", db.pool.store[555]["first_source"] == "ref:5")
check("первый upsert -> last_source == first_source", db.pool.store[555]["last_source"] == "ref:5")
asyncio.run(db.upsert_attribution(555, "src:vk_promo"))
check("повторный upsert НЕ меняет first_source", db.pool.store[555]["first_source"] == "ref:5")
check("повторный upsert меняет last_source", db.pool.store[555]["last_source"] == "src:vk_promo")
check("first_ts <= last_ts после второго вызова",
      db.pool.store[555]["first_ts"] <= db.pool.store[555]["last_ts"])

print("\n[10] Database.upsert_attribution(): pool=None -> no-op, не падает")
db_nopool = Database.__new__(Database)
db_nopool.pool = None
try:
    asyncio.run(db_nopool.upsert_attribution(1, "organic"))
    _ok = True
except Exception:
    _ok = False
check("upsert_attribution без пула не бросает исключение", _ok)

# ═══════════════════════ 11. Database.add_events_batch (мок-СУБД) ═══════════════════════
print("\n[11] Database.add_events_batch(): батч пишется целиком")
db2 = make_fake_db()
batch = [
    {"uid": 1, "event": "session_start", "props": {}, "ts": 1000.0},
    {"uid": 2, "event": "level_up", "props": {"level": 3}, "ts": 1001.0},
]
asyncio.run(db2.add_events_batch(batch))
check("оба события попали в мок-БД", len(db2.pool.events) == 2)

print("\n[12] Database.add_events_batch(): пустой батч и pool=None — no-op")
db3 = make_fake_db()
asyncio.run(db3.add_events_batch([]))
check("пустой список не создаёт запросов", len(db3.pool.events) == 0)
db_nopool2 = Database.__new__(Database)
db_nopool2.pool = None
try:
    asyncio.run(db_nopool2.add_events_batch([{"uid": 1, "event": "death", "props": {}, "ts": 1.0}]))
    _ok2 = True
except Exception:
    _ok2 = False
check("add_events_batch без пула не бросает исключение", _ok2)

# ═══════════════════════ 13. flush_to_db() с инъекцией set_db ═══════════════════════
print("\n[13] flush_to_db(): с set_db() батч реально пишется в БД")
reset_analytics()
db4 = make_fake_db()
analytics.set_db(db4)
analytics.track(9, "shop_view", {"vendor": "храм"})
analytics.track(9, "party_join", {"size": 2})
written2 = asyncio.run(analytics.flush_to_db())
check("flush_to_db() с БД вернул число реально записанных событий", written2 == 2)
check("события действительно долетели до мок-БД", len(db4.pool.events) == 2)
check("после flush_to_db() буфер модуля пуст", analytics.flush() == [])
analytics.set_db(None)   # не оставляем инъекцию висеть для остальных тестов файла

print(f"\nИТОГО: ✅ {_passed} пройдено, ❌ {_failed} провалено")
sys.exit(1 if _failed else 0)
