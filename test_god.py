# -*- coding: utf-8 -*-
"""
Тесты бога-оркестратора (ai/god.py) и прямого запуска событий (engine/events.py).
Запуск из каталога проекта:
    python test_god.py

Без сети и без БД: провайдер ИИ подменяется в самом тесте (monkeypatch функций
provider.enabled/provider.chat на async-заглушки). Проверяет:
  • валидацию JSON бога (корректный проходит, source=llm);
  • фаззинг: битый JSON / чужой event_id / чужая зона / duration вне границ /
    announce 1000 симв. → retry → fallback; markdown-инъекции вычищаются;
  • fallback без ИИ всегда валиден (100 прогонов random);
  • build_summary ≤600 симв. и не падает на пустом мире;
  • events.start: валидные старты / скип при MAX_ACTIVE / кламп duration /
    подбор зоны city/wild / отвержение неизвестного eid;
  • chronicle.set_epic/get_epic + JSON-сериализацию;
  • шаблонную летопись без ИИ (непуста при непустой хронике).
"""
import asyncio
import random
import sys

from ai import god
from ai import provider
from engine import events, chronicle, seasons

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


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# Сохраняем оригиналы провайдера, чтобы восстанавливать между блоками.
_ORIG_ENABLED = provider.enabled
_ORIG_CHAT = provider.chat


def set_ai(enabled: bool, reply):
    """Подменить провайдера: enabled() и chat(). reply — строка ИЛИ функция
    (system, messages, **kw) -> строка/None (для эмуляции retry/ошибок)."""
    provider.enabled = lambda: enabled
    if callable(reply):
        async def _chat(system, messages, tier="mid", max_tokens=220,
                        temperature=0.9, **kw):   # Этап 8: context=/version=
            return reply(system, messages)
        provider.chat = _chat
    else:
        async def _chat(system, messages, tier="mid", max_tokens=220,
                        temperature=0.9, **kw):   # Этап 8: context=/version=
            return reply
        provider.chat = _chat


def restore_ai():
    provider.enabled = _ORIG_ENABLED
    provider.chat = _ORIG_CHAT


# ─────────────────────── 1. build_summary ───────────────────────
print("\n[1] build_summary — сводка мира")
events.reset(); chronicle.reset()
_s_empty = god.build_summary({}, None)
check("build_summary не падает на пустом мире", isinstance(_s_empty, str) and _s_empty)
check("build_summary пустого мира ≤600 симв.", len(_s_empty) <= 600)
check("build_summary содержит счётчик игроков", "Игроки" in _s_empty)
check("build_summary содержит номер сезона", "Сезон" in _s_empty)

# наполненный мир: хроника + активное событие
chronicle.record("boss", "Дракон Пепелища пал от рук героев")
events.ENABLED = True
events.start("туманный_прилив", world=None)
_s_full = god.build_summary({}, None)
check("build_summary с событием упоминает его", "Туманный прилив" in _s_full)
check("build_summary с хроникой не превышает 600", len(_s_full) <= 600)
events.reset(); chronicle.reset()

# _catalog_brief
_cb = god._catalog_brief()
check("_catalog_brief перечисляет все 7 событий", _cb.count("\n") + 1 == 7)
check("_catalog_brief упоминает миграцию стаи", "миграция_стаи" in _cb)


# ─────────────────────── 2. events.start — прямой запуск ───────────────────────
print("\n[2] events.start — валидация и кламп")
from engine.world import World
w = World()

events.reset()
_m, _r = events.start("туманный_прилив", world=w, rng=random.Random(1))
check("валидный старт (глобальная аномалия) возвращает анонс", bool(_m) and _r is None)
check("глобальное событие даёт множители везде", events.modifiers("Пепельные Пустоши")["xp"] > 1.0)

events.reset()
_m, _r = events.start("ярмарка_гильдий", world=w, rng=random.Random(1))
_z = events.active()[0]["zone"]
check("ярмарка_гильдий стартует в городской зоне", _z in events._CITY_ZONES)

events.reset()
_before = sum(len(v) for v in w.mobs.values())
_m, _r = events.start("миграция_стаи", world=w, rng=random.Random(2))
_after = sum(len(v) for v in w.mobs.values())
_z = events.active()[0]["zone"]
check("миграция_стаи стартует в дикой зоне", _z not in events._CITY_ZONES)
check("миграция_стаи спавнит 3–5 зверей", 3 <= (_after - _before) <= 5)

events.reset()
_m, _r = events.start("несуществующее_событие", world=w)
check("неизвестный eid отвергается ([], reason)", _m == [] and _r is not None)

events.reset()
events.start("туманный_прилив", world=w)
_m, _r = events.start("пророчество_глубин", world=w)
check("MAX_ACTIVE: второй старт скипается", _m == [] and "лимит" in (_r or ""))

events.reset()
events.start("туманный_прилив", duration=999999, world=w, now=1000)
_d_hi = events.active()[0]["ends_at"] - 1000
check("duration кламп сверху (≤3600)", _d_hi <= 3600)
events.reset()
events.start("туманный_прилив", duration=1, world=w, now=1000)
_d_lo = events.active()[0]["ends_at"] - 1000
check("duration кламп снизу (≥1800)", _d_lo >= 1800)

events.reset()
# невалидная зона для city-события → движок подбирает валидную
_m, _r = events.start("ярмарка_гильдий", zone="Затонувший Город", world=w, rng=random.Random(3))
_z = events.active()[0]["zone"]
check("невалидная зона → подбор валидной городской", bool(_m) and _z in events._CITY_ZONES)

events.reset()
events.start("пророчество_глубин", world=w)
_mod = events.modifiers("Пепельные Пустоши")
check("пророчество без модификаторов (всё ×1.0)", _mod == {"xp": 1.0, "gold": 1.0, "loot": 1.0})
events.reset()


# ─────────────────────── 3. decide — валидный JSON бога ───────────────────────
print("\n[3] decide — корректное решение LLM проходит")
events.reset(); chronicle.reset()
set_ai(True, '{"event_id":"туманный_прилив","zone":null,"duration_sec":2200,"announce":"Туман поднялся из бездны и укрыл земли."}')
god.reset_budget()
_d = run(god.decide({}, None, rng=random.Random(4)))
check("валидный JSON → source=llm", _d["source"] == "llm")
check("decide возвращает event_id из каталога", _d["event_id"] in events._DEFS)
check("decide: duration в границах события", 1800 <= _d["duration"] <= 3600)
check("decide: announce непуст и ≤220", 0 < len(_d["announce"]) <= 220)
check("decide: announce не содержит markdown-инъекций",
      not any(ch in _d["announce"] for ch in "*_`[]"))


# ─────────────────────── 4. decide — фаззинг → retry → fallback ───────────────────────
print("\n[4] decide — фаззинг битых ответов")

# 4.1 битый JSON на первой попытке, валидный на второй → llm за 2 вызова
_calls = {"n": 0}
def _retry_reply(system, messages):
    _calls["n"] += 1
    if _calls["n"] == 1:
        return "просто болтовня без JSON"
    return '{"event_id":"ярмарка_гильдий","zone":"city","duration_sec":5000,"announce":"Гильдии съехались на торг."}'
set_ai(True, _retry_reply)
god.reset_budget()
_d = run(god.decide({}, None, rng=random.Random(5)))
check("битый JSON → retry даёт валидный llm", _d["source"] == "llm" and _calls["n"] == 2)

# 4.2 чужой event_id всегда → fallback
set_ai(True, '{"event_id":"ДРАКОН_ИЗ_ДРУГОЙ_ИГРЫ","zone":null,"duration_sec":600,"announce":"текст"}')
god.reset_budget()
_d = run(god.decide({}, None, rng=random.Random(6)))
check("чужой event_id → fallback", _d["source"] == "fallback" and _d["event_id"] in events._DEFS)

# 4.3 чужая зона для city-события всегда → fallback
set_ai(True, '{"event_id":"ярмарка_гильдий","zone":"Пепельные Пустоши","duration_sec":4000,"announce":"текст"}')
god.reset_budget()
_d = run(god.decide({}, None, rng=random.Random(7)))
check("чужая зона (не city) → fallback", _d["source"] == "fallback")

# 4.4 duration вне границ — НЕ повод для fallback (движок клампит), source=llm
set_ai(True, '{"event_id":"туманный_прилив","zone":null,"duration_sec":9999999,"announce":"Туман."}')
god.reset_budget()
_d = run(god.decide({}, None, rng=random.Random(8)))
check("duration вне границ клампится, не роняя решение (llm)",
      _d["source"] == "llm" and 1800 <= _d["duration"] <= 3600)

# 4.5 announce 1000 символов + инъекции → обрезка/очистка, source=llm
_long = "*" * 500 + "_злой_ `код` [ссылка] " + "я" * 500
import json as _json
set_ai(True, _json.dumps({"event_id": "пророчество_глубин", "zone": None,
                          "duration_sec": 600, "announce": _long}))
god.reset_budget()
_d = run(god.decide({}, None, rng=random.Random(9)))
check("announce 1000 симв. обрезан ≤220", len(_d["announce"]) <= 220)
check("announce после очистки без спецсимволов",
      not any(ch in _d["announce"] for ch in "*_`[]"))

# 4.6 пустой announce → fallback
set_ai(True, '{"event_id":"туманный_прилив","zone":null,"duration_sec":2000,"announce":"   "}')
god.reset_budget()
_d = run(god.decide({}, None, rng=random.Random(10)))
check("пустой announce → fallback", _d["source"] == "fallback")

# 4.7 chat возвращает None (сбой сети) → fallback
set_ai(True, None)
god.reset_budget()
_d = run(god.decide({}, None, rng=random.Random(11)))
check("chat=None (сбой) → fallback", _d["source"] == "fallback")


# ─────────────────────── 5. Бюджет LLM ───────────────────────
print("\n[5] Бюджет: не чаще 1 вызова LLM в GOD_MIN_INTERVAL")
set_ai(True, '{"event_id":"туманный_прилив","zone":null,"duration_sec":2000,"announce":"Туман встал."}')
god.reset_budget()
_d1 = run(god.decide({}, None, rng=random.Random(12)))
_d2 = run(god.decide({}, None, rng=random.Random(12)))   # сразу же, в пределах интервала
check("первый вызов — llm", _d1["source"] == "llm")
check("второй вызов в интервале — fallback (бюджет)", _d2["source"] == "fallback")
# по истечении интервала — снова llm
_d3 = run(god.decide({}, None, rng=random.Random(12), now=__import__("time").time() + god.GOD_MIN_INTERVAL + 10))
check("после GOD_MIN_INTERVAL — снова llm", _d3["source"] == "llm")


# ─────────────────────── 6. fallback без ИИ — 100 прогонов ───────────────────────
print("\n[6] fallback всегда валиден (100 прогонов random)")
restore_ai()
set_ai(False, None)          # ИИ выключен
_bad = 0
_rng = random.Random(2024)
for _ in range(100):
    _d = run(god.decide({}, None, rng=_rng))
    if (_d["source"] != "fallback" or _d["event_id"] not in events._DEFS
            or not _d["announce"] or len(_d["announce"]) > 220):
        _bad += 1
check("100 прогонов decide без ИИ — все валидны и fallback", _bad == 0)

# fallback_decision напрямую тоже 100x валиден
_bad = 0
for _ in range(100):
    _d = god.fallback_decision(_rng)
    if _d["event_id"] not in events._DEFS or not _d["announce"]:
        _bad += 1
check("100 прогонов fallback_decision — все валидны", _bad == 0)


# ─────────────────────── 7. Летопись сезона ───────────────────────
print("\n[7] epic_chronicle — летопись сезона")
chronicle.reset()
chronicle.record("boss", "Пал Дракон Пепелища")
chronicle.record("season", "Начался сезон 5")

# без ИИ → шаблон, непуст
set_ai(False, None)
_e = run(god.epic_chronicle(4))
check("шаблонная летопись без ИИ непуста при непустой хронике", bool(_e) and len(_e) > 0)
check("шаблонная летопись ≤900 симв.", len(_e) <= 900)
check("шаблонная летопись упоминает номер сезона", "4" in _e)

# пустая хроника без ИИ → всё равно непустой шаблон
chronicle.reset()
_e2 = run(god.epic_chronicle(3))
check("летопись непуста даже на пустой хронике (шаблон)", bool(_e2))

# с ИИ → сохраняет абзацы, ≤900, без markdown-инъекций
chronicle.record("boss", "Событие сезона")
set_ai(True, "Первый абзац про павших.\n\nВторой абзац про фракции.\n\nТретий про Туман.")
_e3 = run(god.epic_chronicle(6))
check("летопись LLM сохраняет разбивку на абзацы", "\n\n" in _e3)
check("летопись LLM ≤900 симв.", len(_e3) <= 900)

# с ИИ но инъекции вычищаются
set_ai(True, "*Опасный* _текст_ с `кодом` и [ссылкой](зло) — абзац.")
_e4 = run(god.epic_chronicle(7))
check("летопись LLM без markdown-инъекций",
      not any(ch in _e4 for ch in "*_`[]"))
restore_ai()


# ─────────────────────── 8. chronicle.set_epic/get_epic + сериализация ───────────────────────
print("\n[8] chronicle: летопись — get/set + JSON-роундтрип")
chronicle.reset()
check("get_epic пуст по умолчанию", chronicle.get_epic() is None)
chronicle.set_epic("Пал последний дракон. Сезон окончен.")
check("set_epic/get_epic работают", chronicle.get_epic() == "Пал последний дракон. Сезон окончен.")
check("render() показывает секцию «🏛 Летопись»", "🏛 *Летопись*" in chronicle.render())

import json
_st = chronicle.export_state()
check("export_state содержит epic", _st.get("epic") == "Пал последний дракон. Сезон окончен.")
# JSON-safe: сериализуемо
_dumped = json.dumps(_st, ensure_ascii=False)
_reloaded = json.loads(_dumped)
chronicle.reset()
chronicle.import_state(_reloaded)
check("import_state восстанавливает epic", chronicle.get_epic() == "Пал последний дракон. Сезон окончен.")
chronicle.set_epic("")
check("set_epic('') сбрасывает летопись", chronicle.get_epic() is None)
chronicle.reset()


# ─────────────────────── ИТОГ ───────────────────────
events.ENABLED = False
events.reset(); chronicle.reset()
print("\n" + "═" * 50)
print(f"ИТОГО: ✅ {_passed} пройдено, ❌ {_failed} провалено")
print("═" * 50)
sys.exit(1 if _failed else 0)
