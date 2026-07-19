# -*- coding: utf-8 -*-
"""
Автономные тесты ретеншен-механик «Эхо Глубин»: стрик входов (engine/streak.py)
и недельные задания (engine/weekly.py), включая интеграцию с ежедневками
(engine/daily.py -> weekly.on_daily_claim); а также сезонный трек наград
(engine/seasons.py: track_*) и коллекции бестиария (engine/bestiary.py:
COLLECTIONS, record_kill), включая их интеграцию с выбором титула
(engine/achievements.py: extra_titles); а также реферальную систему
(engine/referral.py: parse_start_arg, set_referrer, on_level, link).
Без Telegram и без PostgreSQL.

Запуск из каталога проекта:
    python test_retention.py

Все даты передаются параметром today=... — системное время не трогаем
(кроме теста интеграции №14, где daily.claim() принципиально не умеет
принимать today и всегда берёт date.today() — там мы используем реальную
сегодняшнюю дату как значение "сегодня", не подменяя часы системы).
Сезонный трек аналогично принимает now=... вместо системного time.time().
"""
import sys
from datetime import date, timedelta

from engine import streak, weekly, daily, seasons, bestiary, achievements, referral
from engine import reputation, dungeon, craft, combat
from engine.content import MOBS
from engine.character import Character
from engine.world import World, MobInstance

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


# ═══════════════════════ СТРИК ВХОДОВ (engine/streak.py) ═══════════════════════

# ─────────────────────── 1. Новая серия ───────────────────────
print("\n[1] Стрик: новая серия")
ch = new_char()
msgs = streak.touch(ch, today="2026-07-06")
check("первый touch -> days == 1", ch.flags["streak"]["days"] == 1)
msgs2 = streak.touch(ch, today="2026-07-06")
check("повторный touch в тот же день -> []", msgs2 == [])
check("повторный touch в тот же день не растит days", ch.flags["streak"]["days"] == 1)

# ─────────────────────── 2. Продолжение серии ───────────────────────
print("\n[2] Стрик: продолжение 3 дня подряд")
ch = new_char()
streak.touch(ch, today="2026-07-06")
streak.touch(ch, today="2026-07-07")
msgs3 = streak.touch(ch, today="2026-07-08")
check("3 дня подряд -> days == 3", ch.flags["streak"]["days"] == 3)
check("сообщение содержит «3 дня»", any("3 дня" in m for m in msgs3))
check("xp_mult == 1.1 в день достижения порога", streak.xp_mult(ch, today="2026-07-08") == 1.1)
check("xp_mult == 1.0 на следующий день", streak.xp_mult(ch, today="2026-07-09") == 1.0)

# ─────────────────────── 3. Заморозка: пропуск ровно 1 дня в той же ISO-неделе ───────────────────────
print("\n[3] Стрик: заморозка спасает серию")
ch = new_char()
streak.touch(ch, today="2026-07-06")   # Пн W28
streak.touch(ch, today="2026-07-07")   # Вт W28, days=2
days_before_freeze = ch.flags["streak"]["days"]
# пропуск 07-08 (Ср) -> touch 07-09 (Чт), оба в пределах W28
msgs_freeze = streak.touch(ch, today="2026-07-09")
check("сообщение содержит «Заморозка»", any("Заморозка" in m for m in msgs_freeze))
check("days сохранён после заморозки", ch.flags["streak"]["days"] == days_before_freeze)

# ─────────────────────── 4. Вторая заморозка в ту же ISO-неделю невозможна ───────────────────────
print("\n[4] Стрик: вторая заморозка в ту же неделю запрещена")
ch = new_char()
streak.touch(ch, today="2026-07-06")   # Пн W28
streak.touch(ch, today="2026-07-07")   # Вт W28, days=2
streak.touch(ch, today="2026-07-09")   # заморозка #1 (пропуск 07-08), freeze_week = W28
check("freeze_week выставлена на текущую неделю", ch.flags["streak"]["freeze_week"] == "2026-W28")
# ещё один пропуск (07-10) -> touch 07-11, всё ещё W28: заморозка недоступна -> сброс серии
msgs_second = streak.touch(ch, today="2026-07-11")
check("вторая заморозка в ту же неделю невозможна -> days == 1", ch.flags["streak"]["days"] == 1)

# ─────────────────────── 5. На стыке ISO-недель заморозка доступна снова ───────────────────────
print("\n[5] Стрик: на стыке недель заморозка доступна снова (обе прощаются)")
ch = new_char()
streak.touch(ch, today="2026-07-09")   # Чт W28
streak.touch(ch, today="2026-07-10")   # Пт W28, days=2
days_start = ch.flags["streak"]["days"]
# пропуск Сб 07-11 -> touch Вс 07-12 (последний день W28): заморозка №1 в W28
msgs_f1 = streak.touch(ch, today="2026-07-12")
check("заморозка №1 (конец недели W28) сработала", any("Заморозка" in m for m in msgs_f1))
check("freeze_week == W28 после первой заморозки", ch.flags["streak"]["freeze_week"] == "2026-W28")
check("days не изменился после заморозки №1", ch.flags["streak"]["days"] == days_start)
# пропуск Пн 07-13 -> touch Вт 07-14 (уже W29): заморозка №2, теперь в новой неделе
msgs_f2 = streak.touch(ch, today="2026-07-14")
check("заморозка №2 (начало недели W29) тоже сработала", any("Заморозка" in m for m in msgs_f2))
check("freeze_week переключилась на W29", ch.flags["streak"]["freeze_week"] == "2026-W29")
check("days не сброшен ни разу за обе заморозки подряд", ch.flags["streak"]["days"] == days_start)

# ─────────────────────── 6. Порог 7 дней: +2000 золота и «большое_зелье» ───────────────────────
print("\n[6] Стрик: порог 7 дней даёт золото и предмет, без дублей")
ch = new_char()
gold0 = ch.gold
seven_days = ["2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09",
              "2026-07-10", "2026-07-11", "2026-07-12"]
last_msgs = []
for d in seven_days:
    last_msgs = streak.touch(ch, today=d)
check("после 7 дней подряд days == 7", ch.flags["streak"]["days"] == 7)
check("выдано +2000 золота на 7-й день", ch.gold - gold0 == 2000)
check("«большое_зелье» добавлено в инвентарь", "большое_зелье" in ch.inventory)
check("сообщение порога 7 получено на 7-й день", len(last_msgs) > 0)
# продолжение серии не дублирует награду
gold_after_7 = ch.gold
inv_len_after_7 = len(ch.inventory)
streak.touch(ch, today="2026-07-13")
check("день 8: золото не изменилось (нет дубля награды)", ch.gold == gold_after_7)
check("день 8: инвентарь не изменился (нет дубля награды)", len(ch.inventory) == inv_len_after_7)

# ─────────────────────── 7. Сброс открывает пороги заново ───────────────────────
print("\n[7] Стрик: сброс серии открывает пороги заново")
ch = new_char()
streak.touch(ch, today="2026-07-06")
streak.touch(ch, today="2026-07-07")
m3_first = streak.touch(ch, today="2026-07-08")   # days=3, порог получен
check("первое достижение порога 3 дало сообщение", any("3 дня" in m for m in m3_first))
# сброс: пропуск >= 2 дней (07-09, 07-10 пропущены)
streak.touch(ch, today="2026-07-11")
check("серия сброшена -> days == 1", ch.flags["streak"]["days"] == 1)
# новая серия из 3 дней снова должна дать сообщение про порог
streak.touch(ch, today="2026-07-12")
m3_again = streak.touch(ch, today="2026-07-13")
check("новая серия из 3 дней после сброса снова даёт сообщение «3 дня»",
      any("3 дня" in m for m in m3_again))

# ─────────────────────── 8. Пропуск двух и более дней -> days == 1 ───────────────────────
print("\n[8] Стрик: пропуск 2+ дней сбрасывает серию")
ch = new_char()
streak.touch(ch, today="2026-07-06")
streak.touch(ch, today="2026-07-07")   # days=2
streak.touch(ch, today="2026-07-10")   # пропущены 07-08 и 07-09 (2 дня)
check("пропуск двух дней -> days == 1", ch.flags["streak"]["days"] == 1)


# ═══════════════════════ НЕДЕЛЬНИК (engine/weekly.py) ═══════════════════════

# ─────────────────────── 9. ensure детерминирован + ротация ───────────────────────
print("\n[9] Недельник: ensure детерминирован, ротация по неделям")
ch_a = new_char(uid=1)
ch_b = new_char(uid=2)
w_a = weekly.ensure(ch_a, today="2026-07-06")
w_b = weekly.ensure(ch_b, today="2026-07-06")
check("два вызова с одной датой (разные персонажи) дают один id", w_a["id"] == w_b["id"])
w_a_repeat = weekly.ensure(ch_a, today="2026-07-06")
check("повторный ensure с той же датой не меняет id", w_a_repeat["id"] == w_a["id"])

start = date.today()
picked_ids = set()
for i in range(26):
    d = start + timedelta(weeks=i)
    wk = weekly._iso_week(d.isoformat())
    picked_ids.add(weekly._pick(wk))
check("ротация по неделям даёт >1 разных набора на 26 недель", len(picked_ids) > 1)

# ─────────────────────── 10. Прогресс: kill_any / kill_boss / daily_claims ───────────────────────
print("\n[10] Недельник: прогресс задач разных типов")
ch = new_char()
w = weekly.ensure(ch, today="2026-07-06")   # W28 -> week_hunt: kills(kill_any,60) boss(kill_boss,1) daily3(daily_claims,3)
check("подобранный набор W28 содержит все три типа задач", w["id"] == "week_hunt")

weekly.on_kill(ch, {"boss": False}, today="2026-07-06")
check("kill_any растёт от обычного моба", ch.flags["weekly"]["progress"].get("kills") == 1)
weekly.on_kill(ch, None, today="2026-07-06")
check("kill_any растёт даже без mob_meta", ch.flags["weekly"]["progress"].get("kills") == 2)
check("kill_boss не растёт от небоссовых убийств", ch.flags["weekly"]["progress"].get("boss", 0) == 0)

weekly.on_kill(ch, {"boss": True}, today="2026-07-06")
check("kill_boss растёт только от mob_meta {'boss': True}", ch.flags["weekly"]["progress"].get("boss") == 1)

before_daily = ch.flags["weekly"]["progress"].get("daily3", 0)
weekly.on_daily_claim(ch, today="2026-07-06")
check("daily_claims растёт через on_daily_claim", ch.flags["weekly"]["progress"].get("daily3") == before_daily + 1)

# ─────────────────────── 11. Прогресс не растёт выше count и не растёт после claimed ───────────────────────
print("\n[11] Недельник: прогресс капается и не растёт после claimed")
ch = new_char()
weekly.ensure(ch, today="2026-07-06")   # week_hunt: boss count=1
weekly.on_kill(ch, {"boss": True}, today="2026-07-06")
weekly.on_kill(ch, {"boss": True}, today="2026-07-06")
check("прогресс не растёт выше count задачи", ch.flags["weekly"]["progress"]["boss"] == 1)

ch2 = new_char(uid=2)
w2 = weekly.ensure(ch2, today="2026-07-06")
ch2.flags["weekly"]["claimed"] = True
before_claimed = dict(ch2.flags["weekly"]["progress"])
weekly.on_kill(ch2, {"boss": False}, today="2026-07-06")
weekly.on_daily_claim(ch2, today="2026-07-06")
check("прогресс не растёт после claimed", ch2.flags["weekly"]["progress"] == before_claimed)

# ─────────────────────── 12. is_complete / claim ───────────────────────
print("\n[12] Недельник: is_complete только при полном выполнении, claim выдаёт награду")
ch = new_char()
w = weekly.ensure(ch, today="2026-07-06")
s = weekly.WEEKLY[w["id"]]
check("is_complete == False, пока задачи не выполнены", weekly.is_complete(ch, today="2026-07-06") is False)

for t in s["tasks"]:
    for _ in range(t["count"]):
        if t["type"] == "kill_any":
            weekly.on_kill(ch, {"boss": False}, today="2026-07-06")
        elif t["type"] == "kill_boss":
            weekly.on_kill(ch, {"boss": True}, today="2026-07-06")
        elif t["type"] == "daily_claims":
            weekly.on_daily_claim(ch, today="2026-07-06")

check("is_complete == True после выполнения всех задач", weekly.is_complete(ch, today="2026-07-06") is True)

gold0, xp0 = ch.gold, ch.xp
inv_len0 = len(ch.inventory)
res = weekly.claim(ch, today="2026-07-06")
rew = s.get("reward", {})
check("claim увеличивает золото на величину награды", ch.gold - gold0 == rew.get("gold", 0))
check("claim увеличивает опыт на величину награды", ch.xp - xp0 == rew.get("xp", 0))
check("claim добавляет предметы награды в инвентарь", len(ch.inventory) - inv_len0 == len(rew.get("items", [])))
check("claimed выставлен в True", ch.flags["weekly"]["claimed"] is True)

gold_after_claim = ch.gold
res2 = weekly.claim(ch, today="2026-07-06")
check("повторный claim отказывает (не «🎁»)", "🎁" not in res2)
check("повторный claim не выдаёт золото повторно", ch.gold == gold_after_claim)

# ─────────────────────── 13. Новая ISO-неделя сбрасывает прогресс и claimed ───────────────────────
print("\n[13] Недельник: новая ISO-неделя сбрасывает прогресс и claimed")
ch = new_char()
weekly.ensure(ch, today="2026-07-06")   # W28
weekly.on_kill(ch, {"boss": False}, today="2026-07-06")
ch.flags["weekly"]["claimed"] = True
w_new = weekly.ensure(ch, today="2026-07-13")   # W29 — новая неделя
check("прогресс сброшен на новой неделе", w_new["progress"] == {})
check("claimed сброшен на новой неделе", w_new["claimed"] is False)
check("номер недели обновлён", w_new["week"] == "2026-W29")

# ─────────────────────── 14. Интеграция: daily.claim продвигает daily_claims недельника ───────────────────────
print("\n[14] Интеграция: daily.claim -> weekly.on_daily_claim")
# daily.claim() не принимает today и всегда берёт системную date.today() — поэтому
# здесь мы не подменяем часы, а используем реальную сегодняшнюю дату как "сегодня".
today_iso = date.today().isoformat()

# перебором weekly._pick ищем ближайшую (начиная с сегодняшней) ISO-неделю,
# чей набор содержит задачу типа daily_claims
target_week = None
for i in range(26):
    d = date.today() + timedelta(weeks=i)
    wk = weekly._iso_week(d.isoformat())
    pid = weekly._pick(wk)
    s = weekly.WEEKLY.get(pid, {})
    if any(t["type"] == "daily_claims" for t in s.get("tasks", [])):
        target_week = (wk, pid, d)
        break
check("найдена ISO-неделя с задачей daily_claims", target_week is not None)

# берём существующий id ежедневного задания из data/daily.yaml
existing_daily_id = next(iter(daily.DAILY))
daily_quest = daily.DAILY[existing_daily_id]

ch = new_char()
# принудительно проставляем персонажу недельный набор на найденную неделю (без подмены системного времени —
# просто пишем то же значение, которое ensure() вычислил бы сам для этой недели)
ch.flags["weekly"] = {"week": target_week[0], "id": target_week[1], "progress": {}, "claimed": False}
weekly_set = weekly.WEEKLY[target_week[1]]
daily_claim_task = next(t for t in weekly_set["tasks"] if t["type"] == "daily_claims")
progress_before = ch.flags["weekly"]["progress"].get(daily_claim_task["id"], 0)

ch.flags["daily"] = {
    "date": today_iso,
    "id": existing_daily_id,
    "progress": daily_quest["count"],
    "claimed": False,
}

result = daily.claim(ch)
check("daily.claim() возвращает подтверждение награды", "🎁" in result)
progress_after = ch.flags["weekly"]["progress"].get(daily_claim_task["id"], 0)
check("daily.claim продвигает daily_claims задачу недельника",
      progress_after == progress_before + 1)
check("прогресс weekly обновился именно для набора найденной недели",
      ch.flags["weekly"]["id"] == target_week[1])


# ═══════════════════════ СЕЗОННЫЙ ТРЕК (engine/seasons.py: track_*) ═══════════════════════
# Трек — отдельная лесенка наград за очки сезона, не завязанная на seasons.ENABLED.
# Все проверки идут через фиксированный now (см. docstring файла) — время не трогаем.

_FIXED_SEASON_NOW = seasons.SEASON_LENGTH * 200 + 1000   # произвольный, далёкий сезон

# ─────────────────────── 15. claimable растёт с очками ───────────────────────
print("\n[15] Сезонный трек: claimable растёт по мере набора очков")
seasons.ENABLED = True
ch = new_char()
check("на 0 очков ничего не доступно к получению",
      seasons.track_claimable(ch, _FIXED_SEASON_NOW) == [])
seasons.add_points(ch, 100, _FIXED_SEASON_NOW)
check("после 100 очков доступен порог 100",
      seasons.track_claimable(ch, _FIXED_SEASON_NOW) == [100])
seasons.add_points(ch, 400, _FIXED_SEASON_NOW)   # итого 500
check("после 500 очков доступны оба пройденных порога (100 и 250 и 500)",
      seasons.track_claimable(ch, _FIXED_SEASON_NOW) == [100, 250, 500])

# ─────────────────────── 16. claim ровно раз ───────────────────────
print("\n[16] Сезонный трек: claim выдаёт награду один раз")
ch = new_char()
seasons.add_points(ch, 100, _FIXED_SEASON_NOW)
gold0 = ch.gold
res = seasons.track_claim(ch, 100, _FIXED_SEASON_NOW)
check("claim(100) сообщает об успехе («🎁»)", "🎁" in res)
check("claim(100) начисляет ровно 2000 золота (награда порога 100)", ch.gold - gold0 == 2000)
check("100 больше не в списке claimable после забора", 100 not in seasons.track_claimable(ch, _FIXED_SEASON_NOW))
res2 = seasons.track_claim(ch, 100, _FIXED_SEASON_NOW)
check("повторный claim(100) отказывает («Уже забрано»)", "Уже забрано" in res2)
check("повторный claim(100) не начисляет золото повторно", ch.gold == gold0 + 2000)

# ─────────────────────── 17. недостигнутое нельзя забрать ───────────────────────
print("\n[17] Сезонный трек: недостигнутый порог забрать нельзя")
ch = new_char()
seasons.add_points(ch, 50, _FIXED_SEASON_NOW)   # меньше первого порога (100)
gold0 = ch.gold
res = seasons.track_claim(ch, 100, _FIXED_SEASON_NOW)
check("claim недостигнутого порога отказывает («Ещё не достигнуто»)",
      "Ещё не достигнуто" in res)
check("золото не выдано за недостигнутый порог", ch.gold == gold0)

# ─────────────────────── 18. сгорание незабранного при смене сезона ───────────────────────
# Трек хранит своё состояние независимо от лиги (season/ensure) и сверяет свой
# season_id при каждом обращении к track_*, поэтому сброс происходит именно
# в момент первого вызова track_claimable/track_claim/track_render с новым now,
# а не автоматически при add_points (add_points трогает только ch.flags["season"]).
print("\n[18] Сезонный трек: незабранные ступени сгорают при смене сезона")
ch = new_char()
seasons.add_points(ch, 100, _FIXED_SEASON_NOW)
seasons.track_claim(ch, 100, _FIXED_SEASON_NOW)
check("после claim порог 100 в claimed", 100 in ch.flags["season_track"]["claimed"])
_next_season_now = _FIXED_SEASON_NOW + seasons.SEASON_LENGTH
seasons.add_points(ch, 100, _next_season_now)   # новый сезон: очки лиги пересчитаны через ensure
cl_next = seasons.track_claimable(ch, _next_season_now)   # первое обращение к треку в новом сезоне
check("при смене сезона track claimed сгорает (список забранных обнулён)",
      ch.flags["season_track"]["claimed"] == [])
check("season_track.id обновился на новый сезон", ch.flags["season_track"]["id"] == seasons.season_id(_next_season_now))
check("порог 100 снова доступен к получению после сгорания", 100 in cl_next)
res_again = seasons.track_claim(ch, 100, _next_season_now)
check("после сгорания порог 100 можно забрать заново в новом сезоне", "🎁" in res_again)

# ─────────────────────── 19. render не падает ───────────────────────
print("\n[19] Сезонный трек: render не падает при разных состояниях")
ch = new_char()
txt_empty = seasons.track_render(ch, _FIXED_SEASON_NOW)
check("render на пустом треке возвращает непустую строку", isinstance(txt_empty, str) and len(txt_empty) > 0)
check("render на пустом треке помечает все ступени как 🔒", "🔒" in txt_empty and "✅" not in txt_empty)
seasons.add_points(ch, 15000, _FIXED_SEASON_NOW)   # максимум трека
seasons.track_claim(ch, 100, _FIXED_SEASON_NOW)
txt_mixed = seasons.track_render(ch, _FIXED_SEASON_NOW)
check("render с частично забранным треком содержит и ✅, и 🎁",
      "✅" in txt_mixed and "🎁" in txt_mixed)
seasons.ENABLED = False


# ═══════════════════════ КОЛЛЕКЦИИ БЕСТИАРИЯ (engine/bestiary.py) ═══════════════════════

# ─────────────────────── 20. все mob_id из коллекций существуют в MOBS ───────────────────────
print("\n[20] Коллекции: все mob_id из data/collections.yaml существуют в MOBS")
_all_col_mobs = [m for col in bestiary.COLLECTIONS.values() for m in col.get("mobs", [])]
check("в data/collections.yaml описана хотя бы одна коллекция", len(bestiary.COLLECTIONS) > 0)
check("все мобы всех коллекций есть в MOBS (mobs.yaml)",
      all(m in MOBS for m in _all_col_mobs))

# ─────────────────────── 21. сбор коллекции даёт награду один раз ───────────────────────
print("\n[21] Коллекции: полный сбор даёт награду один раз, повторно — нет")
_col_id, _col = next(iter(bestiary.COLLECTIONS.items()))
ch = new_char()
gold0 = ch.gold
inv_len0 = len(ch.inventory)
partial_lines = [bestiary.record_kill(ch, mob_id) for mob_id in _col["mobs"][:-1]]
check(f"неполный сбор «{_col_id}» ни разу не даёт сообщения о завершении",
      all(l == [] for l in partial_lines))
last_mob = _col["mobs"][-1]
final_lines = bestiary.record_kill(ch, last_mob)
check("сбор последнего моба коллекции даёт сообщение «Коллекция … собрана»",
      any("собрана" in l for l in final_lines))
_rew = _col.get("reward", {})
check("золото за коллекцию начислено в ch.gold", ch.gold - gold0 == _rew.get("gold", 0))
check("предметы награды добавлены в инвентарь",
      len(ch.inventory) - inv_len0 == len(_rew.get("items", [])))
check("коллекция помечена как собранная в ch.flags", _col_id in ch.flags.get("collections_done", []))
# повторное убийство того же моба не должно повторно выдавать награду
gold_after = ch.gold
again = bestiary.record_kill(ch, last_mob)
check("повторное убийство моба уже собранной коллекции не даёт сообщения", again == [])
check("повторное убийство не начисляет награду коллекции повторно", ch.gold == gold_after)

# ─────────────────────── 22. коллекция с титулом кладёт его в extra_titles ───────────────────────
print("\n[22] Коллекции: коллекция с титулом добавляет его в extra_titles")
_titled_id, _titled_col = next(((cid, c) for cid, c in bestiary.COLLECTIONS.items() if c.get("title")), (None, None))
check("в data/collections.yaml есть хотя бы одна коллекция с титулом", _titled_col is not None)
ch = new_char()
for mob_id in _titled_col["mobs"]:
    bestiary.record_kill(ch, mob_id)
check("титул коллекции появился в ch.flags['extra_titles']",
      _titled_col["title"] in ch.flags.get("extra_titles", []))

# ─────────────────────── 23. extra_titles валиден для set_title/active_title ───────────────────────
print("\n[23] Коллекции: extra_titles принимается set_title/active_title (engine/achievements.py)")
ch = new_char()
ch.flags["extra_titles"] = ["Испытавший Глубину"]
ok = achievements.set_title(ch, "Испытавший Глубину")
check("set_title принимает титул из extra_titles", ok is True)
check("active_title возвращает титул коллекции", achievements.active_title(ch) == "Испытавший Глубину")
ok_bad = achievements.set_title(ch, "Несуществующий титул")
check("set_title отклоняет титул, которого нет ни в достижениях, ни в extra_titles", ok_bad is False)

# ─────────────────────── 24. record_kill возвращает [] в норме ───────────────────────
print("\n[24] Коллекции: record_kill обычно возвращает пустой список")
ch = new_char()
out = bestiary.record_kill(ch, "крыса")
check("record_kill возвращает список (list)", isinstance(out, list))
check("обычное убийство без завершения коллекции -> []", out == [])
check("счётчик убийств моба увеличился", bestiary.kills(ch, "крыса") == 1)


# ═══════════════════════ РЕФЕРАЛКА (engine/referral.py) ═══════════════════════

# ─────────────────────── 25. parse_start_arg: валидный аргумент ───────────────────────
print("\n[25] Рефералка: parse_start_arg разбирает валидный deep-link")
check("«/start ref_123» -> 123", referral.parse_start_arg("/start ref_123") == 123)
check("«/start ref_0» -> 0", referral.parse_start_arg("/start ref_0") == 0)
check("большой uid разбирается верно", referral.parse_start_arg("/start ref_987654321") == 987654321)

# ─────────────────────── 26. parse_start_arg: мусор и отсутствие аргумента -> None ───────────────────────
print("\n[26] Рефералка: parse_start_arg отбрасывает мусор и пустой /start")
check("голый «/start» -> None", referral.parse_start_arg("/start") is None)
check("пустая строка -> None", referral.parse_start_arg("") is None)
check("None -> None", referral.parse_start_arg(None) is None)
check("«/start abc» (не ref_*) -> None", referral.parse_start_arg("/start abc") is None)
check("«/start ref_abc» (не число) -> None", referral.parse_start_arg("/start ref_abc") is None)
check("«/start ref_» (пустой хвост) -> None", referral.parse_start_arg("/start ref_") is None)
check("«/start ref_12abc» (хвост с буквами) -> None", referral.parse_start_arg("/start ref_12abc") is None)

# ─────────────────────── 27. set_referrer: базовый успех ───────────────────────
print("\n[27] Рефералка: set_referrer назначает реферера при создании персонажа")
newp = new_char(uid=100)
ok = referral.set_referrer(newp, 1)
check("set_referrer возвращает True при успехе", ok is True)
check("ch.flags['ref_by'] проставлен верным uid", newp.flags.get("ref_by") == 1)

# ─────────────────────── 28. set_referrer: нельзя указать самого себя ───────────────────────
print("\n[28] Рефералка: set_referrer отказывает, если реферер == сам игрок")
self_ref = new_char(uid=200)
ok_self = referral.set_referrer(self_ref, 200)
check("set_referrer(ch, ch.uid) -> False", ok_self is False)
check("ref_by не проставлен при попытке указать себя", "ref_by" not in self_ref.flags)

# ─────────────────────── 29. set_referrer: повторно нельзя (уже есть ref_by) ───────────────────────
print("\n[29] Рефералка: set_referrer нельзя вызвать повторно поверх уже назначенного")
dup = new_char(uid=300)
referral.set_referrer(dup, 1)
ok_dup = referral.set_referrer(dup, 999)
check("повторный set_referrer возвращает False", ok_dup is False)
check("ref_by не перезаписан вторым вызовом", dup.flags.get("ref_by") == 1)

# ─────────────────────── 30. set_referrer: ref_uid не int -> отказ ───────────────────────
print("\n[30] Рефералка: set_referrer отказывает при нечисловом ref_uid")
bad_type = new_char(uid=400)
check("ref_uid строкой -> False", referral.set_referrer(bad_type, "1") is False)
check("ref_uid списком -> False", referral.set_referrer(bad_type, [1]) is False)
check("ref_uid None -> False", referral.set_referrer(bad_type, None) is False)
check("ref_by не проставлен ни одной из некорректных попыток", "ref_by" not in bad_type.flags)

# ─────────────────────── 31. on_level: до REWARD_LEVEL — ничего не начисляется ───────────────────────
print("\n[31] Рефералка: on_level ничего не даёт до достижения REWARD_LEVEL")
referrer = new_char(uid=1)
friend = new_char(uid=101)
referral.set_referrer(friend, referrer.uid)
friend.level = referral.REWARD_LEVEL - 1
gold_before, ref_gold_before = friend.gold, referrer.gold
new_lines, ref_line = referral.on_level(friend, referrer)
check("на уровне ниже REWARD_LEVEL new_lines пуст", new_lines == [])
check("на уровне ниже REWARD_LEVEL ref_line пуст (None)", ref_line is None)
check("золото друга не изменилось", friend.gold == gold_before)
check("золото реферера не изменилось", referrer.gold == ref_gold_before)
check("ref_rewarded не выставлен раньше времени", not friend.flags.get("ref_rewarded"))

# ─────────────────────── 32. on_level: на REWARD_LEVEL — награда обоим ровно один раз ───────────────────────
print("\n[32] Рефералка: on_level на REWARD_LEVEL награждает друга и реферера один раз")
referrer2 = new_char(uid=2)
friend2 = new_char(uid=102)
referral.set_referrer(friend2, referrer2.uid)
friend2.level = referral.REWARD_LEVEL
gold0, inv0 = friend2.gold, len(friend2.inventory)
ref_gold0 = referrer2.gold
new_lines2, ref_line2 = referral.on_level(friend2, referrer2)
check("на REWARD_LEVEL friend получает золото NEW_PLAYER_REWARD",
      friend2.gold - gold0 == referral.NEW_PLAYER_REWARD.get("gold", 0))
check("на REWARD_LEVEL friend получает предметы NEW_PLAYER_REWARD",
      len(friend2.inventory) - inv0 == len(referral.NEW_PLAYER_REWARD.get("items", [])))
check("на REWARD_LEVEL new_lines содержит сообщение о награде", len(new_lines2) == 1)
check("на REWARD_LEVEL ref_rewarded выставлен в True", friend2.flags.get("ref_rewarded") is True)
check("на REWARD_LEVEL referrer получает золото REFERRER_REWARD",
      referrer2.gold - ref_gold0 == referral.REFERRER_REWARD.get("gold", 0))
check("на REWARD_LEVEL referrer.ref_count увеличился на 1", referrer2.flags.get("ref_count") == 1)
check("на REWARD_LEVEL ref_line — непустая строка с именем друга",
      isinstance(ref_line2, str) and friend2.name in ref_line2)

# повторный левелап не должен дублировать награду ни одной из сторон
friend2.level = referral.REWARD_LEVEL + 1
gold_after, ref_gold_after = friend2.gold, referrer2.gold
new_lines3, ref_line3 = referral.on_level(friend2, referrer2)
check("повторный левелап: new_lines пуст (нет дубля)", new_lines3 == [])
check("повторный левелап: ref_line пуст (нет дубля)", ref_line3 is None)
check("повторный левелап: золото друга не изменилось", friend2.gold == gold_after)
check("повторный левелап: золото реферера не изменилось", referrer2.gold == ref_gold_after)
check("повторный левелап: ref_count реферера не изменился", referrer2.flags.get("ref_count") == 1)

# ─────────────────────── 33. on_level: без ref_by — начислений нет ───────────────────────
print("\n[33] Рефералка: on_level без ref_by не начисляет ничего")
lone = new_char(uid=103)
lone.level = referral.REWARD_LEVEL
gold_lone = lone.gold
new_lines4, ref_line4 = referral.on_level(lone, None)
check("без ref_by new_lines пуст", new_lines4 == [])
check("без ref_by ref_line пуст", ref_line4 is None)
check("без ref_by золото не изменилось", lone.gold == gold_lone)

# ─────────────────────── 34. MAX_REWARDED: лимит рефереру, но не другу ───────────────────────
print("\n[34] Рефералка: лимит MAX_REWARDED — реферер с полным лимитом награды не получает, друг получает")
maxed_referrer = new_char(uid=3)
maxed_referrer.flags["ref_count"] = referral.MAX_REWARDED
friend3 = new_char(uid=104)
referral.set_referrer(friend3, maxed_referrer.uid)
friend3.level = referral.REWARD_LEVEL
gold_friend0, ref_gold_maxed0 = friend3.gold, maxed_referrer.gold
new_lines5, ref_line5 = referral.on_level(friend3, maxed_referrer)
check("при исчерпанном лимите друг всё равно получает награду",
      friend3.gold - gold_friend0 == referral.NEW_PLAYER_REWARD.get("gold", 0))
check("при исчерпанном лимите new_lines непуст (награда другу выдана)", len(new_lines5) == 1)
check("при исчерпанном лимите referrer НЕ получает золото", maxed_referrer.gold == ref_gold_maxed0)
check("при исчерпанном лимите ref_line пуст (None)", ref_line5 is None)
check("при исчерпанном лимите ref_count реферера не превышает MAX_REWARDED",
      maxed_referrer.flags.get("ref_count") == referral.MAX_REWARDED)

# ─────────────────────── 35. link(): формирует правильный deep-link URL ───────────────────────
print("\n[35] Рефералка: link() формирует корректный URL приглашения")
url = referral.link("echo_deep_bot", 555)
check("link содержит правильный домен t.me", url.startswith("https://t.me/echo_deep_bot"))
check("link содержит параметр start=ref_<uid>", url == "https://t.me/echo_deep_bot?start=ref_555")
check("parse_start_arg корректно разбирает хвост сгенерированной ссылки (после /start )",
      referral.parse_start_arg("/start ref_555") == 555)

# ─────────────────────── 36. render(): экран показывает ссылку, счётчик и условия ───────────────────────
print("\n[36] Рефералка: render() формирует читаемый экран приглашения")
viewer = new_char(uid=105)
viewer.flags["ref_count"] = 3
screen = referral.render(viewer, "echo_deep_bot")
check("render — непустая строка", isinstance(screen, str) and len(screen) > 0)
check("render содержит персональную ссылку игрока",
      f"ref_{viewer.uid}" in screen)
check("render показывает текущее число приглашённых (3)", "3" in screen)
check("render упоминает REWARD_LEVEL (условие награды)", str(referral.REWARD_LEVEL) in screen)


# ═══════════════ НЕДЕЛЬНИК: РАЗНООБРАЗИЕ ЦЕЛЕЙ (Этап 6.1) ═══════════════
# Новые типы задач data/weekly.yaml: heal_ally, dungeon_group, explore,
# craft_item, sell_lot, faction_rep, dtype_kill, event_talk — движковые хуки
# в engine/combat.py, engine/dungeon.py, engine/craft.py, engine/reputation.py,
# engine/weekly.py (on_room_visit) и engine/loop.py (dtype_kill-условие).

# ─────────────────────── 37. Каталог: типы задач data/weekly.yaml известны движку ───────────────────────
print("\n[37] Недельник: каталог типов задач в data/weekly.yaml валиден")
_KILL_TYPES = {"kill_any", "kill_boss"}
_NEW_TYPES = weekly.KNOWN_TYPES - _KILL_TYPES - {"daily_claims"}
_all_types_known = all(
    t["type"] in weekly.KNOWN_TYPES
    for s in weekly.WEEKLY.values() for t in s["tasks"]
)
check("все типы задач во всех наборах data/weekly.yaml есть в weekly.KNOWN_TYPES", _all_types_known)
check("в data/weekly.yaml ровно 8 наборов", len(weekly.WEEKLY) == 8)
_kill_counts = {sid: sum(1 for t in s["tasks"] if t["type"] in _KILL_TYPES)
                for sid, s in weekly.WEEKLY.items()}
check("week_hunt (легаси, обратная совместимость с W28) — единственный набор с 2 kill-задачами",
      _kill_counts.get("week_hunt") == 2
      and all(v <= 1 for sid, v in _kill_counts.items() if sid != "week_hunt"))
check("каждый набор, кроме week_hunt, содержит ≥2 задачи из НОВЫХ типов Этапа 6.1",
      all(sum(1 for t in s["tasks"] if t["type"] in _NEW_TYPES) >= 2
          for sid, s in weekly.WEEKLY.items() if sid != "week_hunt"))
check("каждый набор содержит ровно одну daily_claims-задачу (держит суточный ритм)",
      all(sum(1 for t in s["tasks"] if t["type"] == "daily_claims") == 1
          for s in weekly.WEEKLY.values()))

# Временный тестовый набор со ВСЕМИ новыми типами (Этап 6.1) — инжектируется
# в глобальный weekly.WEEKLY (по образцу QUESTS["t_..."] в test_quest_types.py),
# чтобы прогресс через реальные движковые хуки был детерминирован независимо
# от реальной календарной даты прогона теста (ротация _pick идёт по хешу
# ISO-недели — без инъекции пришлось бы подбирать даты под текущий календарь).
_TID = "t_weekly_diverse_6_1"
weekly.WEEKLY[_TID] = {
    "name": "Тестовый: все новые типы",
    "tasks": [
        {"id": "heal", "type": "heal_ally", "count": 2, "name": "Исцелить союзников 2 раза"},
        {"id": "dungeon", "type": "dungeon_group", "count": 1, "name": "Пройти подземелье в группе"},
        {"id": "explore", "type": "explore", "count": 2, "name": "Обойти 2 новых комнаты"},
        {"id": "craft", "type": "craft_item", "count": 1, "name": "Сковать 1 предмет"},
        {"id": "sell", "type": "sell_lot", "count": 1, "name": "Продать лот"},
        {"id": "rep", "type": "faction_rep", "count": 50, "name": "Заслужить 50 репутации"},
        {"id": "dtype", "type": "dtype_kill", "count": 1, "name": "Одолеть врага его слабостью"},
        {"id": "talk", "type": "event_talk", "count": 1, "name": "Поговорить во время события"},
    ],
    "reward": {"xp": 500, "gold": 5000, "items": []},
}
_now_week = weekly._iso_week()


def _pin_test_weekly(ch):
    """Привязать персонажа к тестовому набору _TID на текущую реальную ISO-неделю
    (совпадает с тем, что вычислят внутри реальные движковые хуки без today=...)."""
    ch.flags["weekly"] = {"week": _now_week, "id": _TID, "progress": {}, "claimed": False}


chd = new_char(uid=300)
_pin_test_weekly(chd)

# ─────────────────────── 38. heal_ally: лечение СОЮЗНИКА скиллом (engine/combat.py:use_skill) ───────────────────────
print("\n[38] Недельник: heal_ally — исцеление союзника скиллом (combat.use_skill)")
chd.loadout = ["blessing"]      # heal/target=allies, доступно независимо от cls
ally = new_char(uid=301)
ally.hp = 1
w_heal = World()
chd.mp = 10000       # с запасом, чтобы не зависеть от ресурс-скейла класса
chd.cooldowns["blessing"] = 0
ok38, ev38 = combat.use_skill(chd, "blessing", w_heal, [chd, ally])
check("blessing применился успешно", ok38)
check("союзник исцелён (hp вырос)", ally.hp > 1)
check("heal_ally: прогресс вырос на 1 от первого лечения союзника",
      chd.flags["weekly"]["progress"].get("heal") == 1)
chd.cooldowns["blessing"] = 0
ally.hp = 1
ok38b, ev38b = combat.use_skill(chd, "blessing", w_heal, [chd, ally])
check("heal_ally: прогресс достиг count=2 после второго лечения",
      chd.flags["weekly"]["progress"].get("heal") == 2)
check("heal_ally: сообщение о выполнении задачи попало в вывод скилла",
      any("📌" in l for l in ev38b))
solo_healer = new_char(uid=309)
_pin_test_weekly(solo_healer)
solo_healer.loadout = ["blessing"]
solo_healer.mp = 10000
combat.use_skill(solo_healer, "blessing", w_heal, [solo_healer])   # лечит только себя
check("heal_ally: лечение САМОГО СЕБЯ прогресс не даёт (нужен именно союзник)",
      solo_healer.flags["weekly"]["progress"].get("heal", 0) == 0)

# ─────────────────────── 39. dungeon_group: босс данжа убит В ГРУППЕ (engine/dungeon.py:on_kill) ───────────────────────
print("\n[39] Недельник: dungeon_group — босс подземелья убит в группе (dungeon.on_kill)")
_boss_mob = dungeon.DUNGEONS["mines"]["boss_mob"]
chd.flags["dungeon_run"] = "mines"
dungeon.on_kill(chd, _boss_mob, group=False)     # соло-забег — группа не засчитана
check("dungeon_group: соло-прохождение (group=False) прогресс НЕ даёт",
      chd.flags["weekly"]["progress"].get("dungeon", 0) == 0)
chd.flags["dungeon_run"] = "mines"
_dg_lines = dungeon.on_kill(chd, _boss_mob, group=True)     # групповой забег (≥2 killers)
check("dungeon_group: групповое прохождение (group=True) засчитано",
      chd.flags["weekly"]["progress"].get("dungeon") == 1)
check("dungeon_group: строка выполнения попала в вывод dungeon.on_kill",
      any("📌" in l for l in _dg_lines))

# ─────────────────────── 40. explore: посещение НОВОЙ комнаты (engine/weekly.py:on_room_visit) ───────────────────────
print("\n[40] Недельник: explore — новые комнаты (weekly.on_room_visit)")
_wexp1 = weekly.on_room_visit(chd, "тестовая_комната_А")
check("explore: первое посещение новой комнаты продвигает прогресс",
      chd.flags["weekly"]["progress"].get("explore") == 1)
_wexp_repeat = weekly.on_room_visit(chd, "тестовая_комната_А")
check("explore: повторное посещение той же комнаты прогресс НЕ даёт",
      _wexp_repeat is None and chd.flags["weekly"]["progress"].get("explore") == 1)
_wexp2 = weekly.on_room_visit(chd, "тестовая_комната_Б")
check("explore: вторая НОВАЯ комната завершает задачу (count=2)",
      chd.flags["weekly"]["progress"].get("explore") == 2 and _wexp2 is not None)
check("explore: посещённые комнаты сохранены в ch.flags['visited_rooms']",
      {"тестовая_комната_А", "тестовая_комната_Б"} <= set(chd.flags.get("visited_rooms", [])))

# ─────────────────────── 41. craft_item: успешный крафт у кузнеца (engine/craft.py:craft) ───────────────────────
print("\n[41] Недельник: craft_item — успешная ковка (craft.craft)")
from engine.content import RECIPES
chd.gold = RECIPES["craft_chainmail"]["gold"] + 100000
chd.inventory += ["железная_руда"] * 5
ok41, msg41 = craft.craft(chd, "craft_chainmail")
check("craft_chainmail успешно скован", ok41 and "кольчуга" in chd.inventory)
check("craft_item: прогресс засчитан (count=1, задача выполнена)",
      chd.flags["weekly"]["progress"].get("craft") == 1)
check("craft_item: сообщение о выполнении задачи добавлено к результату ковки",
      "📌" in msg41)

# ─────────────────────── 42. sell_lot: лот продан на аукционе (engine/weekly.py:on_sell_lot) ───────────────────────
print("\n[42] Недельник: sell_lot — продажа лота на аукционе (weekly.on_sell_lot)")
_wsell = weekly.on_sell_lot(chd)
check("sell_lot: прогресс засчитан продавцу (count=1, задача выполнена)",
      chd.flags["weekly"]["progress"].get("sell") == 1 and _wsell is not None)

# ─────────────────────── 43. faction_rep: прирост репутации фракции (engine/reputation.py:gain) ───────────────────────
print("\n[43] Недельник: faction_rep — прирост репутации фракции (reputation.gain)")
_wrep = reputation.gain(chd, "orden_rassveta", 50)
check("faction_rep: репутация начислена персонажу", reputation.points(chd, "orden_rassveta") == 50)
check("faction_rep: прогресс засчитан ровно на величину прироста (count=50)",
      chd.flags["weekly"]["progress"].get("rep") == 50)
check("faction_rep: reputation.gain вернул строку выполнения задачи", _wrep is not None and "📌" in _wrep)
_rep_neg = new_char(uid=302)
_pin_test_weekly(_rep_neg)
_wrep_neg = reputation.gain(_rep_neg, "orden_rassveta", -10)
check("faction_rep: ОТРИЦАТЕЛЬНЫЙ прирост (штраф) прогресс недельника не двигает",
      _wrep_neg is None and _rep_neg.flags["weekly"]["progress"].get("rep", 0) == 0)

# ─────────────────────── 44. dtype_kill: удар по уязвимости моба (engine/combat.py + engine/loop.py) ───────────────────────
print("\n[44] Недельник: dtype_kill — урон точно по уязвимости моба")
mob_vuln = MobInstance("t:скелет:1", "скелет", "тест_комната")     # нежить: vuln holy/fire
mob_safe = MobInstance("t:скелет:2", "скелет", "тест_комната")
combat._mark_exploit(mob_vuln, chd.uid, "holy")     # holy ∈ vuln нежити
combat._mark_exploit(mob_safe, chd.uid, "bash")     # bash не в vuln/resist нежити
check("dtype_kill: удар holy по нежити помечает exploited_by", chd.uid in mob_vuln.exploited_by)
check("dtype_kill: удар bash по нежити НЕ помечает exploited_by", chd.uid not in mob_safe.exploited_by)
# точное воспроизведение условия из engine/loop.py:on_mob_death
if chd.uid in mob_safe.exploited_by:
    weekly.on_dtype_kill(chd)
check("dtype_kill: без эксплойта уязвимости прогресс не растёт",
      chd.flags["weekly"]["progress"].get("dtype", 0) == 0)
_wdt = None
if chd.uid in mob_vuln.exploited_by:
    _wdt = weekly.on_dtype_kill(chd)
check("dtype_kill: с эксплойтом уязвимости прогресс засчитан (count=1)",
      chd.flags["weekly"]["progress"].get("dtype") == 1)
check("dtype_kill: получена строка выполнения задачи", _wdt is not None and "📌" in _wdt)

# ─────────────────────── 45. event_talk: разговор с NPC во время мирового события ───────────────────────
print("\n[45] Недельник: event_talk — разговор во время мирового события (weekly.on_event_talk)")
_wtalk = weekly.on_event_talk(chd)
check("event_talk: прогресс засчитан (count=1, задача выполнена)",
      chd.flags["weekly"]["progress"].get("talk") == 1 and _wtalk is not None)

# ─────────────────────── 46. Полный набор из НОВЫХ задач: is_complete/claim ───────────────────────
print("\n[46] Недельник: набор из новых задач выполнен целиком — is_complete/claim")
check("все 8 задач тестового набора выполнены — is_complete == True",
      weekly.is_complete(chd) is True)
_gold0, _xp0 = chd.gold, chd.xp
_res46 = weekly.claim(chd)
check("claim выдал награду («🎁» в ответе)", "🎁" in _res46)
check("claim начислил золото награды", chd.gold - _gold0 == 5000)
check("claim начислил опыт награды", chd.xp - _xp0 == 500)
check("claimed выставлен в True", chd.flags["weekly"]["claimed"] is True)

# уборка временного набора — не должен влиять на другие возможные прогоны/импорты
weekly.WEEKLY.pop(_TID, None)


print(f"\nИТОГО: ✅ {_passed} пройдено, ❌ {_failed} провалено")
sys.exit(1 if _failed else 0)
