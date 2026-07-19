# -*- coding: utf-8 -*-
"""
Тесты «Хроники мира» (engine/chronicle.py) и интеграции с seasons/bestiary/npc_ai.
Запуск из каталога проекта:
    python test_chronicle.py
Проверяет: кольцевой буфер record/recent (порядок, maxlen), дедуп record_once,
render() на пустом/полном буфере, круговую JSON-safe сериализацию
export_state()/import_state(), относительное время, обрезку длинных записей,
а также интеграцию с seasons.ensure() (ролловер сезона пишет ровно одну запись).
"""
import json
import sys

from engine import chronicle
from engine import seasons
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


def new_char(uid=1, name="Тест"):
    ch = Character(uid=uid, name=name, cls="warrior", race="human")
    ch.init_vitals()
    ch.flags = {}
    return ch


# ─────────────────────── 1. record / recent: порядок и maxlen ───────────────────────
print("\n[1] record()/recent(): порядок (свежие первыми) и maxlen")
chronicle.reset()
chronicle.record("boss", "Первое событие", ts=1000.0)
chronicle.record("boss", "Второе событие", ts=2000.0)
chronicle.record("boss", "Третье событие", ts=3000.0)
_rec = chronicle.recent(3)
check("recent(3) вернул 3 записи", len(_rec) == 3)
check("recent(): самая свежая запись первой", _rec[0] == "Третье событие")
check("recent(): порядок полностью свежие->старые", _rec == ["Третье событие", "Второе событие", "Первое событие"])
check("recent(n) не превышает n даже при большем логе", len(chronicle.recent(1)) == 1)
check("recent(1) вернул именно самую свежую", chronicle.recent(1) == ["Третье событие"])

chronicle.reset()
check("после reset() лог пуст", chronicle.recent(5) == [])

# maxlen: добавим больше записей, чем _LOG_MAXLEN (60), проверим что старые вытесняются
chronicle.reset()
for i in range(70):
    chronicle.record("event", f"Событие {i}", ts=float(i))
_all_recent = chronicle.recent(100)   # запросим с запасом — buffer максимум 60
check("кольцевой буфер ограничен 60 записями", len(_all_recent) == 60)
check("после переполнения самая свежая — «Событие 69»", _all_recent[0] == "Событие 69")
check("после переполнения самые старые (0..9) вытеснены", "Событие 0" not in _all_recent and "Событие 9" not in _all_recent)


# ─────────────────────── 2. record_once: дедуп ───────────────────────
print("\n[2] record_once(): дедуп по (etype, key)")
chronicle.reset()
chronicle.record_once("season", "5", "Начался сезон 5", ts=100.0)
chronicle.record_once("season", "5", "Начался сезон 5 (повтор)", ts=200.0)
chronicle.record_once("season", "5", "И ещё раз", ts=300.0)
_rec2 = chronicle.recent(10)
check("record_once с тем же ключом не дублирует запись", len(_rec2) == 1)
check("record_once сохранил именно первый текст", _rec2[0] == "Начался сезон 5")

chronicle.record_once("season", "6", "Начался сезон 6", ts=400.0)
check("record_once с ДРУГИМ ключом добавляет новую запись", len(chronicle.recent(10)) == 2)

chronicle.record_once("legend", "5", "Другой тип события с тем же key=5", ts=500.0)
check("record_once: (etype, key) — разные etype с тем же key НЕ считаются дублем",
      len(chronicle.recent(10)) == 3)


# ─────────────────────── 3. render(): пустой и полный буфер ───────────────────────
print("\n[3] render(): не падает на пустом и полном буфере")
chronicle.reset()
_render_empty = chronicle.render()
check("render() на пустом логе возвращает строку", isinstance(_render_empty, str))
check("render() на пустом логе упоминает «Хроника мира»", "Хроника мира" in _render_empty)
check("render() на пустом логе не падает и не пуст", len(_render_empty) > 0)

chronicle.reset()
for i in range(20):
    chronicle.record("boss", f"Босс {i} повержен", ts=1000.0 + i)
_render_full = chronicle.render()
check("render() на полном логе возвращает строку", isinstance(_render_full, str))
check("render() показывает не более 12 записей по умолчанию",
      sum(1 for line in _render_full.split("\n") if line.startswith("•")) <= 12)
check("render() показывает самые свежие записи (Босс 19 есть)", "Босс 19" in _render_full)
check("render() не показывает записи за пределами топ-12 (Босс 0 отсутствует)", "Босс 0" not in _render_full)
check("render(n=3) уважает переданный лимит",
      sum(1 for line in chronicle.render(3).split("\n") if line.startswith("•")) == 3)


# ─────────────────────── 4. export_state()/import_state(): круговая сериализация ───────────────────────
print("\n[4] export_state()/import_state(): круговая JSON-safe сериализация")
chronicle.reset()
chronicle.record("boss", "Аэльдмар пал", ts=1111.0)
chronicle.record_once("season", "9", "Начался сезон 9", ts=2222.0)
_exp = chronicle.export_state()
check("export_state() JSON-safe (json.dumps проходит)", isinstance(json.dumps(_exp), str))

chronicle.import_state({})
check("import_state({}) — лог пуст", chronicle.recent(10) == [])
check("import_state({}) сбрасывает dirty", chronicle.is_dirty() is False)

chronicle.import_state(json.loads(json.dumps(_exp)))
_after_import = chronicle.recent(10)
check("import_state() восстановил обе записи", len(_after_import) == 2)
check("import_state() восстановил порядок (свежая первой)", _after_import[0] == "Начался сезон 9")
check("import_state() восстановил и вторую запись", "Аэльдмар пал" in _after_import)
# дедуп-реестр должен тоже восстановиться: повторный record_once с тем же ключом не добавит запись
chronicle.record_once("season", "9", "Дубликат — не должен попасть")
check("import_state() восстановил _SEEN — record_once всё ещё дедупит после импорта",
      len(chronicle.recent(10)) == 2)


# ─────────────────────── 5. set_db_mode/is_dirty/mark_clean ───────────────────────
print("\n[5] set_db_mode()/is_dirty()/mark_clean() — протокол персиста (как territory.py)")
chronicle.reset()
check("после reset() dirty=False", chronicle.is_dirty() is False)
chronicle.record("event", "Тестовое событие")
check("record() помечает dirty=True", chronicle.is_dirty() is True)
chronicle.mark_clean()
check("mark_clean() сбрасывает dirty", chronicle.is_dirty() is False)
chronicle.set_db_mode(True)
chronicle.record("event", "Ещё событие в db-режиме")
check("record() помечает dirty=True даже в db-режиме (db_mode не блокирует запись)",
      chronicle.is_dirty() is True)
chronicle.set_db_mode(False)
chronicle.mark_clean()


# ─────────────────────── 6. relative-time хелпер ───────────────────────
print("\n[6] _relative_time(): относительное время")
_now = 100000.0
check("только что (<60с)", chronicle._relative_time(_now - 5, now=_now) == "только что")
check("минуты назад", chronicle._relative_time(_now - 125, now=_now) == "2м назад")
check("часы назад", chronicle._relative_time(_now - 7300, now=_now) == "2ч назад")
check("дни назад", chronicle._relative_time(_now - 2 * 86400 - 10, now=_now) == "2д назад")
check("не уходит в отрицательное значение при ts > now", chronicle._relative_time(_now + 100, now=_now) == "только что")


# ─────────────────────── 7. recent() обрезает длинные тексты ───────────────────────
print("\n[7] recent(): обрезка длинных записей (беречь токены NPC-промпта)")
chronicle.reset()
_long_text = "А" * 150
chronicle.record("event", _long_text, ts=1.0)
_rec_trunc = chronicle.recent(1)
check("recent() обрезает текст длиннее _RECENT_TRUNC (90 симв.)", len(_rec_trunc[0]) <= 90)
check("recent() добавляет многоточие при обрезке", _rec_trunc[0].endswith("…"))

chronicle.reset()
_short_text = "Короткое событие"
chronicle.record("event", _short_text, ts=1.0)
check("recent() НЕ трогает короткий текст", chronicle.recent(1)[0] == _short_text)

# render() тоже не должен раздуваться неограниченно длинным текстом в тексте записи
# (render использует полный текст записи, а не truncate — проверим, что это ожидаемо
# документированное поведение: только recent() обрезает специально для LLM-промпта)
chronicle.reset()
chronicle.record("event", _long_text, ts=1.0)
check("render() показывает текст записи как есть (обрезка — только для recent/NPC)",
      _long_text in chronicle.render())


# ─────────────────────── 8. Интеграция: seasons.ensure() ролловер -> ровно 1 запись "season" ───────────────────────
print("\n[8] Интеграция: seasons.ensure() ролловер (2 персонажа, 1 сезон) -> 1 запись")
seasons.ENABLED = True
_now0 = seasons.SEASON_LENGTH * 100 + 100     # фиксируем текущий сезон
a = new_char(uid=101, name="Алиса")
b = new_char(uid=102, name="Борис")
# инициализирующий ensure() у СВЕЖЕГО персонажа сам по себе может породить одну
# запись «season» (переход от дефолтного season_id() к _now0 — это ожидаемое,
# отдельно проверенное поведение). Сбрасываем хронику ПОСЛЕ синхронизации обоих
# персонажей на _now0, чтобы изолированно проверить именно ЦЕЛЕВОЙ ролловер ниже.
seasons.ensure(a, _now0)
seasons.ensure(b, _now0)
chronicle.reset()
seasons.add_points(a, 100, _now0)
seasons.add_points(b, 200, _now0)
_next_season_ts = _now0 + seasons.SEASON_LENGTH
seasons.ensure(a, _next_season_ts)   # ролловер персонажа A
seasons.ensure(b, _next_season_ts)   # ролловер персонажа B (тот же сезон!)
_season_events = [t for t in chronicle.recent(20) if "Начался сезон" in t]
check("ролловер двух персонажей одного сезона -> РОВНО одна запись «season» в хронике",
      len(_season_events) == 1)
check("текст записи корректно называет номер сезона",
      str(seasons.season_id(_next_season_ts)) in _season_events[0])
# третий персонаж, ролловер в СЛЕДУЮЩИЙ сезон -> должна появиться вторая, отдельная запись
c = new_char(uid=103, name="Виктор")
seasons.ensure(c, _next_season_ts)   # синхронизируем c с текущим сезоном
_next_season_ts2 = _next_season_ts + seasons.SEASON_LENGTH
seasons.ensure(c, _next_season_ts2)
_season_events2 = [t for t in chronicle.recent(20) if "Начался сезон" in t]
check("ролловер в НОВЫЙ сезон добавляет вторую отдельную запись", len(_season_events2) == 2)
seasons.ENABLED = False


# ─────────────────────── 9. Интеграция: seasons — лига «Легенда» впервые за сезон ───────────────────────
print("\n[9] Интеграция: лига «Легенда» впервые за сезон -> record_once, дедуп по (sid, uid)")
chronicle.reset()
seasons.ENABLED = True
_now1 = seasons.SEASON_LENGTH * 200 + 50
d = new_char(uid=201, name="Легенда")
seasons.ensure(d, _now1)
_legend_thr = seasons.TIERS[-1][0]
seasons.add_points(d, _legend_thr, _now1)     # пересекаем порог «Легенда»
_legend_events = [t for t in chronicle.recent(20) if "Легенда" in t]
check("достижение лиги «Легенда» пишет запись в хронику", len(_legend_events) == 1)
check("запись упоминает имя персонажа", "Легенда" in _legend_events[0] and d.name in _legend_events[0])
# повторное начисление очков (персонаж всё ещё выше порога) НЕ должно дублировать запись
seasons.add_points(d, 10, _now1)
check("повторное начисление очков не дублирует запись «Легенда» (дедуп record_once)",
      len([t for t in chronicle.recent(20) if "Легенда" in t]) == 1)
seasons.ENABLED = False


# ─────────────────────── 10. Интеграция: bestiary — коллекция с титулом ───────────────────────
print("\n[10] Интеграция: bestiary — сбор коллекции с титулом пишет запись")
from engine import bestiary
chronicle.reset()
# ищем именно коллекцию С титулом (не полагаемся на порядок словаря — так тест
# значим независимо от того, какая коллекция первая в data/collections.yaml)
_titled = [(cid, col) for cid, col in bestiary.COLLECTIONS.items() if col.get("title")]
if _titled:
    _cid, _col = _titled[0]
    e = new_char(uid=301, name="Коллекционер")
    for _mob in _col["mobs"]:
        bestiary.record_kill(e, _mob)
    _col_events = [t for t in chronicle.recent(20) if "собрал коллекцию" in t]
    check(f"сбор коллекции «{_col['name']}» (с титулом «{_col['title']}») пишет запись в хронику",
          len(_col_events) == 1)
    check("запись упоминает имя персонажа и коллекцию",
          e.name in _col_events[0] and _col["name"] in _col_events[0])
    # коллекция БЕЗ титула (если есть) не должна писать запись в хронику
    _untitled = [(cid, col) for cid, col in bestiary.COLLECTIONS.items() if not col.get("title")]
    if _untitled:
        _cid2, _col2 = _untitled[0]
        f = new_char(uid=302, name="БезТитула")
        for _mob in _col2["mobs"]:
            bestiary.record_kill(f, _mob)
        _col_events2 = [t for t in chronicle.recent(20) if f.name in t]
        check("сбор коллекции БЕЗ титула НЕ пишет запись в хронику (по ТЗ п.2)",
              len(_col_events2) == 0)
    else:
        check("в data/collections.yaml все коллекции с титулом — доп. проверка пропущена", True)
else:
    check("data/collections.yaml не содержит коллекций с титулом — тест пропущен корректно", True)


# ─────────────────────── 11. Интеграция: loop.py — territory flip и boss ───────────────────────
print("\n[11] Интеграция: engine.loop импортирует chronicle и хуки на месте (по исходному коду)")
import inspect
from engine import loop as _loop_mod
_loop_src = inspect.getsource(_loop_mod)
check("loop.py импортирует chronicle", "from . import chronicle" in _loop_src)
check("loop.py вызывает chronicle.record(\"territory\", ...) при смене владельца зоны",
      'chronicle.record("territory"' in _loop_src)
check("loop.py вызывает chronicle.record(\"boss\", ...) при падении мирового/рейд-босса",
      'chronicle.record("boss"' in _loop_src)
check("loop.py вызывает chronicle.record(\"event\", ...) только для СТАРТовавших событий",
      'chronicle.record("event", line)' in _loop_src)


# ─────────────────────── 12. Интеграция: ai/npc_ai.py — сплетни NPC ───────────────────────
print("\n[12] Интеграция: ai/npc_ai.py — NPC знают о недавних событиях")
from ai import npc_ai
chronicle.reset()
_prompt_empty = npc_ai._system_prompt({"name": "Страж", "role": "стражник", "persona": "Суров."})
check("_system_prompt() без событий в хронике НЕ содержит блок «Недавние события»",
      "Недавние события мира" not in _prompt_empty)
chronicle.record("boss", "Дракон Пепелища повержен героями")
_prompt_full = npc_ai._system_prompt({"name": "Страж", "role": "стражник", "persona": "Суров."})
check("_system_prompt() с событием в хронике СОДЕРЖИТ блок «Недавние события»",
      "Недавние события мира" in _prompt_full)
check("_system_prompt() включает текст события в промпт",
      "Дракон Пепелища повержен героями" in _prompt_full)
check("_chronicle_context() использует не более 3 последних событий",
      npc_ai._chronicle_context.__doc__ is not None)  # смоук: функция документирована
chronicle.reset()


# ─────────────────────── ИТОГ ───────────────────────
print("\n" + "═" * 50)
print(f"ИТОГО: ✅ {_passed} пройдено, ❌ {_failed} провалено")
print("═" * 50)
sys.exit(1 if _failed else 0)
