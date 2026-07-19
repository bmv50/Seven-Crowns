# -*- coding: utf-8 -*-
"""Тесты долгой памяти ИИ-NPC (ai/memory.py) — без сети и без реальной БД.
Запуск: python test_memory.py

Проверяет: чистую функцию ранжирования rank() (recency+lex_sim, top-k,
стабильность), fallback store()/retrieve() без БД (ch.flags['npc_mem2'] +
легаси-строка ch.flags['npc_mem']), работу через мок-БД (аналог engine.db),
no-op поведение engine.db.Database с pool=None, и интеграцию с
ai/npc_ai._system_prompt (маркированный блок памяти «— ...»)."""
import asyncio
import sys
import time

from ai import memory
from ai import npc_ai
from engine.character import Character
from engine.db import Database

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
    c = Character(uid=uid, name="Тест", cls="warrior", race="human")
    c.init_vitals()
    return c


NOW = 2_000_000.0

# ═══════════════════════ [1] rank() — чистое ранжирование ═══════════════════════
print("\n[1] rank(): свежесть + лексическая похожесть")

# свежая запись бьёт старую при РАВНОЙ похожести (обе 0, т.к. запрос ни с чем не пересекается)
_recs_fresh_old = [
    (NOW - 1 * 86400, "текст А про меч"),
    (NOW - 10 * 86400, "текст Б про меч"),
]
_top = memory.rank(_recs_fresh_old, "совершенно другое дело", NOW, k=1)
check("равная похожесть (0) -> побеждает более свежая запись",
      _top == ["текст А про меч"])

# похожая бьёт свежую при БОЛЬШОЙ разнице похожести (recency-разрыв небольшой)
_query = "где найти ключ от подвала"
_recs_sim_vs_fresh = [
    (NOW - 1 * 86400, "искал ключ от подвала"),      # чуть старше, но высокая похожесть
    (NOW, "просто болтали о погоде"),                  # свежее, но похожести нет
]
_top2 = memory.rank(_recs_sim_vs_fresh, _query, NOW, k=1)
check("большая разница похожести перевешивает малую разницу свежести",
      _top2 == ["искал ключ от подвала"])

# query=None -> чистая свежесть, лексика не участвует
_recs_none = [
    (NOW - 5 * 86400, "буквально про ключ ключ ключ"),
    (NOW, "нечто совсем не похожее"),
]
_top3 = memory.rank(_recs_none, None, NOW, k=1)
check("query=None -> побеждает более свежая запись независимо от текста",
      _top3 == ["нечто совсем не похожее"])

# top-k и порядок (свежие впереди при query=None)
_recs_5 = [(NOW - i * 86400, f"запись-{i}") for i in range(5)]  # 0 самая свежая
_top_k = memory.rank(_recs_5, None, NOW, k=3)
check("top-k=3 возвращает 3 самые свежие записи по убыванию свежести",
      _top_k == ["запись-0", "запись-1", "запись-2"])

# стабильность порядка при равных очках (одинаковый ts, query=None)
_recs_tie = [(NOW, "первая"), (NOW, "вторая"), (NOW, "третья")]
_top_tie = memory.rank(_recs_tie, None, NOW, k=3)
check("при равенстве очков сохраняется исходный порядок записи",
      _top_tie == ["первая", "вторая", "третья"])

# ═══════════════════════ [2] store() — fallback без БД ═══════════════════════
print("\n[2] store(): fallback-зеркало в ch.flags['npc_mem2'] (без БД)")
memory.set_db(None)   # убедимся, что БД не подключена для этого блока
ch2 = new_char(uid=200)
npc = "старейшина"

for i in range(7):
    asyncio.run(memory.store(ch2, npc, f"воспоминание номер {i} " + "x" * 20))
_lst = ch2.flags.get("npc_mem2", {}).get(npc, [])
check("fallback-список не превышает 5 записей", len(_lst) == 5)
check("хранятся 5 ПОСЛЕДНИХ записей (старые вытеснены)",
      _lst[-1].startswith("воспоминание номер 6"))
check("каждая запись в fallback обрезана до 120 символов", all(len(x) <= 120 for x in _lst))

_ch_long = new_char(uid=201)
_very_long = "б" * 250
asyncio.run(memory.store(_ch_long, npc, _very_long))
_saved = _ch_long.flags["npc_mem2"][npc][0]
check("текст длиннее 200 символов итоговый fallback-элемент всё равно ≤120",
      len(_saved) <= 120)

# ═══════════════════════ [3] retrieve() — fallback без БД ═══════════════════════
print("\n[3] retrieve(): fallback + легаси-строка npc_mem как самая старая запись")
memory.set_db(None)
ch3 = new_char(uid=300)
check("пустая память -> retrieve возвращает []",
      asyncio.run(memory.retrieve(ch3, npc, None, k=3, now=NOW)) == [])

for i in range(3):
    asyncio.run(memory.store(ch3, npc, f"новое {i}"))
ch3.flags.setdefault("npc_mem", {})[npc] = "легаси: игрок когда-то искал старый клинок"

_res = asyncio.run(memory.retrieve(ch3, npc, None, k=10, now=NOW + 100))
check("легаси-строка попадает в выдачу как одна из записей",
      any("легаси" in t for t in _res))
check("легаси-строка ранжируется как самая старая (последняя при query=None)",
      _res[-1].startswith("легаси"))

# суммарный размер блока ограничен 350 символами
ch4 = new_char(uid=301)
for i in range(5):
    asyncio.run(memory.store(ch4, npc, ("длинная запись номер %d " % i) + "я" * 110))
_res_big = asyncio.run(memory.retrieve(ch4, npc, None, k=5, now=NOW + 200))
check("совокупный размер блока памяти ≤350 символов",
      sum(len(t) for t in _res_big) <= 350)

# ═══════════════════════ [4] мок-БД: store/retrieve через «Postgres» ═══════════════════════
print("\n[4] Мок-БД (список вместо pool): store/retrieve сквозь БД-слой")


class FakeDB:
    """Мини-заглушка engine.db.Database — та же сигнатура методов, без asyncpg."""

    def __init__(self):
        self.pool = object()   # truthy -> считается «подключённой»
        self.rows = {}         # (uid, npc_id) -> [(ts, text), ...]

    async def add_npc_memory(self, uid, npc_id, text):
        self.rows.setdefault((uid, npc_id), []).append((time.time(), text))

    async def get_npc_memories(self, uid, npc_id, limit=20):
        recs = sorted(self.rows.get((uid, npc_id), []), key=lambda r: -r[0])
        return recs[:limit]


_fake = FakeDB()
memory.set_db(_fake)
ch5 = new_char(uid=500)
try:
    asyncio.run(memory.store(ch5, npc, "запись через мок-БД"))
    check("store() записал в мок-БД (не только в fallback)",
          len(_fake.rows.get((500, npc), [])) == 1)
    check("store() ВСЕГДА зеркалит в fallback, даже когда БД доступна",
          ch5.flags.get("npc_mem2", {}).get(npc) == ["запись через мок-БД"])

    _too_long = "д" * 250
    asyncio.run(memory.store(ch5, npc, _too_long))
    _db_text = _fake.rows[(500, npc)][-1][1]
    check("текст длиннее 200 символов обрезается перед записью в БД",
          len(_db_text) == 200)

    _res_db = asyncio.run(memory.retrieve(ch5, npc, "запись", k=5, now=time.time() + 1))
    check("retrieve() при доступной БД читает записи оттуда",
          any("запись через мок-БД" in t for t in _res_db))
finally:
    memory.set_db(None)

# no-op проверка чистой части слоя БД (pool=None -> без исключений, [] / None)
_db_noop = Database()   # connect() не вызывался -> pool остаётся None
check("Database.pool по умолчанию None (без подключения)", _db_noop.pool is None)
asyncio.run(_db_noop.add_npc_memory(1, npc, "x"))   # не должно бросить исключение
check("add_npc_memory с pool=None — no-op без исключений", True)
check("get_npc_memories с pool=None -> []",
      asyncio.run(_db_noop.get_npc_memories(1, npc)) == [])
asyncio.run(_db_noop.prune_npc_memories(1, npc))    # тоже no-op
check("prune_npc_memories с pool=None — no-op без исключений", True)

# ═══════════════════════ [5] интеграция с ai/npc_ai._system_prompt ═══════════════════════
print("\n[5] _system_prompt: блок памяти из списка mems (маркеры «— »)")
_ctx = {"name": "Хальдер", "role": "questgiver", "persona": "Старый.", "knowledge": []}

_p_list = npc_ai._system_prompt(_ctx, memory=["помнит про ключ", "помнит про туман"])
check("список воспоминаний даёт маркированные строки «— »",
      "— помнит про ключ" in _p_list and "— помнит про туман" in _p_list)
check("заголовок блока памяти присутствует", "помнишь:" in _p_list)

_p_empty = npc_ai._system_prompt(_ctx, memory=[])
check("пустой список воспоминаний -> блока памяти нет", "помнишь" not in _p_empty)

_p_none = npc_ai._system_prompt(_ctx, memory=None)
check("memory=None -> блока памяти нет (как раньше)", "помнишь" not in _p_none)

# легаси-строка по-прежнему поддерживается напрямую (обратная совместимость сигнатуры)
_p_legacy = npc_ai._system_prompt(_ctx, memory="старая строка-воспоминание")
check("строка (легаси) по-прежнему форматируется как раньше",
      "помнишь: старая строка-воспоминание" in _p_legacy)

# ─────────────────────── ИТОГ ───────────────────────
print("\n" + "═" * 50)
print(f"ИТОГО: ✅ {_passed} пройдено, ❌ {_failed} провалено")
print("═" * 50)
sys.exit(1 if _failed else 0)
