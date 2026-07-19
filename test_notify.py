# -*- coding: utf-8 -*-
"""
Автономные тесты push-реактивации (engine/notify.py) — без Telegram и БД.
Запуск:
    python test_notify.py
Проверяет: суточную квоту, тихие часы (дроп босса / перенос ежедневки),
настройки категорий, очередь emit/due и отсутствие импорта aiogram в модуле.
"""
import os
import sys
import time
from datetime import datetime

from engine import notify
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
    return Character(uid=uid, name="Тест", cls="warrior", race="human")


def ts(y=2026, mo=7, d=2, h=12, mi=0):
    """unix-время для заданного локального часа (тихие часы — по локали)."""
    return datetime(y, mo, d, h, mi, 0).timestamp()


notify.ENABLED = True   # для тестов очереди

# ─────────────────────── 1. НАСТРОЙКИ КАТЕГОРИЙ ───────────────────────
print("\n[1] Настройки категорий")
ch = new_char()
check("дефолт — все категории включены", all(notify.enabled(ch, c) for c in notify.CATEGORIES))
notify.set_pref(ch, "world_boss", False)
check("выключение сохраняется", notify.enabled(ch, "world_boss") is False)
check("прочие остаются включёнными", notify.enabled(ch, "daily_reset") is True)
new_state = notify.toggle_pref(ch, "world_boss")
check("toggle возвращает новое состояние", new_state is True and notify.enabled(ch, "world_boss"))
check("каталог содержит все 10 категорий", len(notify.CATEGORIES) == 10)
check("категория world_event присутствует", "world_event" in notify.CATEGORIES)
check("у каждой категории есть подпись", all(c in notify.LABELS for c in notify.CATEGORIES))

# ─────────────────────── 2. ТИХИЕ ЧАСЫ ───────────────────────
print("\n[2] Тихие часы 23:00–09:00")
check("23:00 — тихо", notify.is_quiet(ts(h=23)))
check("03:00 — тихо", notify.is_quiet(ts(h=3)))
check("08:59 — тихо", notify.is_quiet(ts(h=8, mi=59)))
check("09:00 — НЕ тихо", notify.is_quiet(ts(h=9)) is False)
check("12:00 — НЕ тихо", notify.is_quiet(ts(h=12)) is False)
check("22:59 — НЕ тихо", notify.is_quiet(ts(h=22, mi=59)) is False)

_night = ts(h=2)
ch = new_char()
check("world_boss в тихие часы -> drop", notify.allow(ch, "world_boss", _night) == "drop")
check("daily_reset в тихие часы -> defer", notify.allow(ch, "daily_reset", _night) == "defer")
check("dungeon_ready в тихие часы -> defer", notify.allow(ch, "dungeon_ready", _night) == "defer")
check("rested_full в тихие часы -> defer", notify.allow(ch, "rested_full", _night) == "defer")
check("auction_sold в тихие часы всё равно -> send", notify.allow(ch, "auction_sold", _night) == "send")

# next_morning
_nm = notify.next_morning(_night)
check("next_morning указывает на 09:00", datetime.fromtimestamp(_nm).hour == 9)
check("next_morning в будущем", _nm > _night)
_late = ts(h=23, mi=30)
check("после 23:00 next_morning — утро следующего дня", notify.next_morning(_late) > _late)

# ─────────────────────── 3. СУТОЧНАЯ КВОТА ───────────────────────
print("\n[3] Квота: не более 2 push/сутки")
_day = ts(h=12)
ch = new_char()
check("свежий игрок — квота 2", notify.quota_left(ch, _day) == 2)
check("1-й daily_reset -> send", notify.allow(ch, "daily_reset", _day) == "send")
notify._quota_bump(ch, _day)
check("после 1 отправки квота 1", notify.quota_left(ch, _day) == 1)
notify._quota_bump(ch, _day)
check("после 2 отправок квота 0", notify.quota_left(ch, _day) == 0)
check("3-й push сверх лимита -> drop", notify.allow(ch, "world_boss", _day) == "drop")
check("но auction_sold — вне лимита -> send", notify.allow(ch, "auction_sold", _day) == "send")
check("и auction_outbid — вне лимита -> send", notify.allow(ch, "auction_outbid", _day) == "send")

# сброс по дате
_next_day = ts(d=3, h=12)
check("новый день — квота снова 2", notify.quota_left(ch, _next_day) == 2)
check("после сброса даты push снова проходит", notify.allow(ch, "world_boss", _next_day) == "send")

# выключенная категория дропается даже при наличии квоты
ch2 = new_char(uid=2)
notify.set_pref(ch2, "world_boss", False)
check("выключенная категория -> drop", notify.allow(ch2, "world_boss", _day) == "drop")

# ─────────────────────── 4. ОЧЕРЕДЬ emit/due ───────────────────────
print("\n[4] Очередь emit/due")
notify.clear()
ch = new_char(uid=10)
chars = {10: ch}
notify.emit(10, "daily_reset", "текст-1")
check("emit кладёт в очередь", notify.pending() == 1)
_ready = notify.due(_day, chars)
check("due отдаёт готовую запись", len(_ready) == 1 and _ready[0]["text"] == "текст-1")
check("после выдачи очередь пуста", notify.pending() == 0)
check("выдача учтена в квоте", notify.quota_left(ch, _day) == 1)

# fire_at в будущем — не отдаётся сейчас
notify.clear()
notify.emit(10, "daily_reset", "позже", fire_at=_day + 3600)
check("запись с будущим fire_at не отдаётся", len(notify.due(_day, chars)) == 0)
check("и остаётся в очереди", notify.pending() == 1)
check("после наступления fire_at — отдаётся", len(notify.due(_day + 3601, {10: new_char(uid=10)})) == 1)

# defer в тихие часы: world_boss дропается, daily_reset переносится
notify.clear()
ch = new_char(uid=11)
notify.emit(11, "world_boss", "босс ночью")
notify.emit(11, "daily_reset", "ежедневка ночью")
_r = notify.due(_night, {11: ch})
check("ночью world_boss не отдан (drop)", all(x["category"] != "world_boss" for x in _r))
check("ночью daily_reset не отдан (defer)", all(x["category"] != "daily_reset" for x in _r))
check("daily_reset остался в очереди (перенос)", notify.pending() == 1)
_kept = notify.due(_night, {11: ch})   # всё ещё ночь — снова не отдаём
check("перенесённый на 09:00 не отдаётся ночью", len(_kept) == 0)

# broadcast (uid=None) — политика на стороне bot, отдаётся как есть
notify.clear()
notify.emit_broadcast("boss-broadcast", "всем")
_rb = notify.due(_day, {})
check("broadcast отдаётся (uid=None)", len(_rb) == 1 and _rb[0]["uid"] is None)

# оффлайн-игрок (нет в chars) — отдаётся, bot решит по БД
notify.clear()
notify.emit(999, "auction_sold", "оффлайн-продажа")
_ro = notify.due(_day, {})
check("оффлайн-игроку запись отдаётся", len(_ro) == 1 and _ro[0]["uid"] == 999)

# ─────────────────────── 5. ЧИСТОТА СЛОЯ ───────────────────────
print("\n[5] engine/notify.py не знает про aiogram")
_src = open(os.path.join(os.path.dirname(__file__), "engine", "notify.py"),
            encoding="utf-8").read()
check("нет импорта aiogram в notify.py", "aiogram" not in _src)
check("нет прямых обращений к telegram", "telegram" not in _src.lower())

# ─────────────────────── 6. ЛИМИТ-ПРЕСЕТЫ (1/2/5) ───────────────────────
print("\n[6] Персональный лимит push: пресеты 1/2/5, дефолт 2")
ch = new_char(uid=20)
check("дефолт лимита — 2", notify.limit(ch) == 2)
check("дефолт совпадает с DEFAULT_LIMIT", notify.limit(ch) == notify.DEFAULT_LIMIT)
check("набор пресетов — (1, 2, 5)", notify.LIMIT_PRESETS == (1, 2, 5))

# цикл 1 -> 2 -> 5 -> 1
new1 = notify.cycle_limit(ch)
check("цикл 2->5", new1 == 5)
new2 = notify.cycle_limit(ch)
check("цикл 5->1", new2 == 1)
new3 = notify.cycle_limit(ch)
check("цикл 1->2", new3 == 2)
new4 = notify.cycle_limit(ch)
check("цикл снова 2->5 (полный круг)", new4 == 5)

# лимит=1 соблюдается квотой
ch_lim1 = new_char(uid=21)
notify.set_pref(ch_lim1, "world_boss", True)
ch_lim1.flags.setdefault("notify", {})["limit"] = 1
check("limit() читает пресет 1 из флагов", notify.limit(ch_lim1) == 1)
check("свежий день — квота равна лимиту (1)", notify.quota_left(ch_lim1, _day) == 1)
check("1-й push при лимите 1 -> send", notify.allow(ch_lim1, "daily_reset", _day) == "send")
notify._quota_bump(ch_lim1, _day)
check("после 1 отправки квота 0 (лимит=1)", notify.quota_left(ch_lim1, _day) == 0)
check("2-й push при лимите 1 -> drop", notify.allow(ch_lim1, "world_boss", _day) == "drop")

# лимит=5 соблюдается квотой (пропускает 5, режет 6-й)
ch_lim5 = new_char(uid=22)
ch_lim5.flags.setdefault("notify", {})["limit"] = 5
check("limit() читает пресет 5 из флагов", notify.limit(ch_lim5) == 5)
check("свежий день — квота равна лимиту (5)", notify.quota_left(ch_lim5, _day) == 5)
for i in range(5):
    verdict = notify.allow(ch_lim5, "world_boss", _day)
    check(f"push #{i+1}/5 при лимите 5 -> send", verdict == "send")
    notify._quota_bump(ch_lim5, _day)
check("после 5 отправок квота 0 (лимит=5)", notify.quota_left(ch_lim5, _day) == 0)
check("6-й push при лимите 5 -> drop", notify.allow(ch_lim5, "world_boss", _day) == "drop")

# повреждённые/нестандартные данные лимита не роняют логику — откат на дефолт
ch_bad = new_char(uid=23)
ch_bad.flags.setdefault("notify", {})["limit"] = 999
check("значение лимита вне пресетов -> откат на дефолт (2)", notify.limit(ch_bad) == 2)
ch_bad2 = new_char(uid=24)
ch_bad2.flags.setdefault("notify", {})["limit"] = "не_число"
check("нечисловое значение лимита -> откат на дефолт (2)", notify.limit(ch_bad2) == 2)

# смена лимита сохраняется независимо от других игроков (нет утечки состояния)
ch_a = new_char(uid=25); ch_b = new_char(uid=26)
notify.cycle_limit(ch_a)   # 2 -> 5
check("лимит игрока A изменился (2->5)", notify.limit(ch_a) == 5)
check("лимит игрока B не затронут", notify.limit(ch_b) == 2)

# ─────────────────────── 7. ТУМБЛЕР ТИХИХ ЧАСОВ (quiet_off) ───────────────────────
print("\n[7] quiet_off отключает тихие часы персонально")
ch_q = new_char(uid=30)
check("дефолт — quiet_off выключен (тихие часы действуют)", notify.quiet_off(ch_q) is False)
check("без quiet_off ночью is_quiet=True", notify.is_quiet(_night, ch_q) is True)
check("is_quiet без указания персонажа — прежнее поведение (совместимость)",
      notify.is_quiet(_night) is True)

new_off = notify.toggle_quiet_off(ch_q)
check("toggle_quiet_off включает тумблер, возвращает True", new_off is True)
check("quiet_off(ch) теперь True", notify.quiet_off(ch_q) is True)
check("с quiet_off ночью is_quiet=False", notify.is_quiet(_night, ch_q) is False)
check("днём is_quiet всё равно False (тривиально)", notify.is_quiet(_day, ch_q) is False)

# при quiet_off=True world_boss ночью больше не дропается — идёт в общую квоту
ch_q2 = new_char(uid=31)
notify.set_quiet_off(ch_q2, True)
check("world_boss ночью с quiet_off -> send (не drop)",
      notify.allow(ch_q2, "world_boss", _night) == "send")
check("daily_reset ночью с quiet_off -> send (не defer)",
      notify.allow(ch_q2, "daily_reset", _night) == "send")

# обратное выключение тумблера возвращает обычное поведение тихих часов
back = notify.toggle_quiet_off(ch_q2)
check("повторный toggle возвращает False", back is False)
check("после повторного toggle world_boss ночью снова -> drop",
      notify.allow(ch_q2, "world_boss", _night) == "drop")

# тумблер не влияет на других персонажей
ch_other = new_char(uid=32)
check("quiet_off другого игрока не затронут", notify.quiet_off(ch_other) is False)
check("для другого игрока тихие часы по-прежнему действуют",
      notify.is_quiet(_night, ch_other) is True)

# ─────────────────────── 8. ДЕДУП rested_full ПО ДНЮ ───────────────────────
print("\n[8] rested_full: триггер в engine/loop.py, дедуп раз в сутки")
import asyncio as _aio_rf
from engine.loop import GameLoop
from engine.world import World as _World
from engine.content import WORLD as _WORLD_CFG

notify.clear()
notify.ENABLED = True

async def _noop_rf(*a, **k):
    pass

_rest_char = new_char(uid=40)
_rest_char.level = 5
_rest_char.room = "temple"     # комната с rest: true в data/world.yaml
check("комната temple помечена rest:true в контенте", bool(_WORLD_CFG["temple"].get("rest")))

_gl_rf = GameLoop(_World(), {40: _rest_char}, _noop_rf, _noop_rf)
_cap = _rest_char.level * 200     # cap = 1000
_rest_char.flags["rested"] = _cap - 5     # cur+8 >= cap -> должен сработать триггер

check("до тика очередь notify пуста", notify.pending() == 0)
_aio_rf.get_event_loop().run_until_complete(_gl_rf.tick())
check("после достижения капа rested_full эмитится в очередь", notify.pending() == 1)
check("категория события — rested_full",
      notify.pending() == 1 and notify._QUEUE[0]["category"] == "rested_full")
check("флаг дедупа notify_rested_day проставлен днём", "notify_rested_day" in _rest_char.flags)
_today_str = _rest_char.flags.get("notify_rested_day")

# повторный тик в тот же день с уже полным баком — не спамит повторно
notify.clear()
_aio_rf.get_event_loop().run_until_complete(_gl_rf.tick())
check("повторный тик в тот же день — очередь остаётся пустой (дедуп)", notify.pending() == 0)
check("флаг дедупа не изменился (та же дата)",
      _rest_char.flags.get("notify_rested_day") == _today_str)

# на следующий день дедуп сбрасывается (симулируем сменой флага на вчера)
notify.clear()
_rest_char.flags["notify_rested_day"] = "2000-01-01"
_rest_char.flags["rested"] = _cap - 5   # снова пересекаем порог капа
_aio_rf.get_event_loop().run_until_complete(_gl_rf.tick())
check("на новый день (иная дата в флаге) триггер срабатывает снова", notify.pending() == 1)

# без notify.ENABLED — триггер молчит (поведение игры не меняется без флага)
notify.clear()
notify.ENABLED = False
_rest_char2 = new_char(uid=41)
_rest_char2.level = 5
_rest_char2.room = "temple"
_rest_char2.flags["rested"] = _rest_char2.level * 200 - 5
_gl_rf2 = GameLoop(_World(), {41: _rest_char2}, _noop_rf, _noop_rf)
_aio_rf.get_event_loop().run_until_complete(_gl_rf2.tick())
check("ENABLED=False -> rested всё равно копится (кап достигнут)",
      _rest_char2.flags.get("rested") == _rest_char2.level * 200)
check("ENABLED=False -> notify-очередь остаётся пустой", notify.pending() == 0)
check("ENABLED=False -> флаг дедупа не проставляется",
      "notify_rested_day" not in _rest_char2.flags)

notify.ENABLED = True   # вернуть для дальнейших секций теста (если появятся)

# отключаем обратно, чтобы не влиять на другие импорты
notify.ENABLED = False

# ─────────────────────── ИТОГ ───────────────────────
print("\n" + "═" * 50)
print(f"ИТОГО: ✅ {_passed} пройдено, ❌ {_failed} провалено")
print("═" * 50)
sys.exit(1 if _failed else 0)
