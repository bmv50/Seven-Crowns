# -*- coding: utf-8 -*-
"""
Доска «ищу группу» и час сбора (engine/lfg.py).

Зачем эта система вообще: мультиплеер заявлен главным хуком, но мир — 291
комната, а онлайн на бете — единицы. Случайно встретить живого игрока нельзя,
поэтому пати/гильдии/арена/кооп-боссы стоят пустыми. Тесты закрепляют два
свойства, ради которых модуль написан: игрока можно НАЙТИ (окно уровней,
свежесть) и до него можно ДОЙТИ (маршрут по комнатам).

Запуск: python run_tests.py lfg
"""
import sys

from engine import lfg, notify
from engine.character import Character
from engine.content import WORLD

_passed = 0
_failed = 0


def check(name, cond, extra=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✅ {name}")
    else:
        _failed += 1
        print(f"  ❌ {name}" + (f"  → {extra}" if extra else ""))


def mk(uid, name, cls="warrior", level=10, room="village"):
    ch = Character(uid=uid, name=name, cls=cls, race="human", level=level)
    ch.room = room
    ch.init_vitals()
    return ch


T0 = 1_000_000.0


# ─────────────────── 1. Запись на доске: жизненный цикл ───────────────────
print("\n[1] Запись на доске: встать, обновить, выйти, протухнуть")
lfg.clear()
a = mk(1, "Аста", level=12)
b = mk(2, "Борн", "priest", level=14, room="mine_entrance")

check("изначально доска пуста", lfg.entries(T0) == [])
lfg.join(a, "иду на босса", now=T0)
check("после join игрок в поиске", lfg.is_listed(1, T0))
check("комментарий сохранён", lfg.entries(T0)[0]["note"] == "иду на босса")
check("чужой uid в поиске не значится", not lfg.is_listed(999, T0))

lfg.join(a, "x" * (lfg.NOTE_MAX + 50), now=T0)
check("комментарий обрезается по NOTE_MAX",
      len(lfg.entries(T0)[0]["note"]) == lfg.NOTE_MAX)
check("повторный join не задваивает запись", len(lfg.entries(T0)) == 1)

check("запись жива до истечения TTL", lfg.is_listed(1, T0 + lfg.TTL - 1))
check("после TTL запись протухает сама", not lfg.is_listed(1, T0 + lfg.TTL + 1))

lfg.clear()
lfg.join(a, now=T0)
check("leave убирает из поиска", lfg.leave(1) and not lfg.is_listed(1, T0))
check("повторный leave возвращает False", lfg.leave(1) is False)


# ─────────────────── 2. Окно уровней ───────────────────
print("\n[2] Доска показывает тех, с кем реально играть вместе")
lfg.clear()
me = mk(1, "Аста", level=20)
near = mk(2, "Борн", level=20 + lfg.LEVEL_WINDOW)
far = mk(3, "Веда", level=20 + lfg.LEVEL_WINDOW + 1)
lfg.join(near, now=T0)
lfg.join(far, now=T0)
seen = {e["uid"] for e in lfg.board_for(me, now=T0)}
check("игрок на границе окна показывается", 2 in seen)
check("игрок за окном отфильтрован", 3 not in seen, seen)

lfg.join(me, now=T0)
check("себя на доске не показываем",
      1 not in {e["uid"] for e in lfg.board_for(me, now=T0)})


# ─────────────────── 3. Маршрут: до игрока можно ДОЙТИ ───────────────────
print("\n[3] Маршрут по комнатам (главное свойство: не список, а путь)")
lfg.clear()
me = mk(1, "Аста", level=12, room="village")
there = mk(2, "Борн", level=12, room="mine_entrance")
lfg.join(there, now=T0)
row = lfg.board_for(me, now=T0)[0]
check("к игроку найден маршрут", row["route"] is not None, row)
check("длина маршрута совпадает с числом шагов", row["steps"] == len(row["route"]))
check("маршрут состоит из направлений мира",
      all(d in ("север", "юг", "восток", "запад", "вверх", "вниз")
          for d in row["route"]), row["route"])

# маршрут действительно ведёт из комнаты в комнату
cur = me.room
for d in row["route"]:
    cur = WORLD[cur]["exits"][d]
check("пройдя маршрут, попадаем ровно в комнату игрока", cur == there.room, cur)

same = mk(3, "Гор", level=12, room="village")
lfg.join(same, now=T0)
row_same = next(e for e in lfg.board_for(me, now=T0) if e["uid"] == 3)
check("для игрока в той же комнате маршрут пустой", row_same["steps"] == 0)
check("ближние сортируются выше дальних",
      [e["uid"] for e in lfg.board_for(me, now=T0)][0] == 3)


# ─────────────────── 4. Комната обновляется, пока игрок в поиске ───────────────────
print("\n[4] Ищущий двигается — доска это видит")
lfg.clear()
walker = mk(2, "Борн", level=12, room="village")
lfg.join(walker, now=T0)
walker.room = "mine_entrance"
lfg.touch_room(walker)
check("touch_room обновил комнату записи",
      lfg.entries(T0)[0]["room"] == "mine_entrance")
lfg.touch_room(mk(77, "Никто"))     # не в поиске — не должно падать
check("touch_room для игрока вне поиска безопасен", len(lfg.entries(T0)) == 1)


# ─────────────────── 5. Час сбора ───────────────────
print("\n[5] Час сбора: раз в сутки, по локальному времени игрока")
lfg.clear()
notify.clear()
_was = notify.ENABLED
notify.ENABLED = True
try:
    ch = mk(1, "Аста", level=12)
    notify.set_tz_offset(ch, 0)     # считаем в UTC, чтобы тест не зависел от машины

    def at_hour(h):
        """unix-время, у которого UTC-час равен h."""
        return 1_700_000_000 - (1_700_000_000 % 86400) + h * 3600

    off = at_hour((lfg.GATHER_HOUR + 3) % 24)
    check("вне часа сбора приглашение не полагается", not lfg.gather_due(ch, off))

    hit = at_hour(lfg.GATHER_HOUR)
    check("в час сбора приглашение полагается", lfg.gather_due(ch, hit))

    sent = lfg.tick_gather({1: ch}, hit)
    check("tick_gather отправил одно приглашение", sent == 1)
    check("повторно в тот же день не шлём", lfg.tick_gather({1: ch}, hit) == 0)
    check("в очереди notify появилась запись категории gather",
          any(x["category"] == "gather" for x in notify._QUEUE), notify._QUEUE)
    check("категория gather объявлена в notify.CATEGORIES",
          "gather" in notify.CATEGORIES)
    check("у категории gather есть человекочитаемая метка",
          bool(notify.LABELS.get("gather")))

    ch.flags.pop("gather_day", None)
    notify.ENABLED = False
    notify.clear()
    check("при выключенном notify ничего не шлётся",
          lfg.tick_gather({1: ch}, hit) == 0 and not notify._QUEUE)

    check("точка сбора существует в мире", lfg.GATHER_ROOM in WORLD)
    check("текст приглашения называет место",
          WORLD[lfg.GATHER_ROOM]["name"] in lfg.gather_text())
finally:
    notify.ENABLED = _was
    notify.clear()
    lfg.clear()


print("\n" + "=" * 56)
print(f"ИТОГО: ✅ {_passed} пройдено, ❌ {_failed} провалено")
print("=" * 56)
sys.exit(1 if _failed else 0)
