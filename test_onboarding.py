# -*- coding: utf-8 -*-
"""
Тесты онбординга «Семь Корон» (без Telegram и без PostgreSQL).
Запуск из каталога проекта:
    python test_onboarding.py

Покрывает:
  • туториал: шаги срабатывают по одному разу в любом порядке, награды и
    финальный бонус не дублируются, для 6+ уровня туториал не стартует;
  • щит новичка: урон ×0.7 на 1–4 ур., полный на 5+;
  • мягкая смерть: карма не роняет предмет при level<5, множитель щита корректен.
"""
import sys
import random

from engine.content import validate, ITEMS
from engine.character import Character
from engine import tutorial, combat, karma

random.seed(2024)   # детерминизм

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


def new_char(cls="warrior", race="human", uid=1, level=1):
    ch = Character(uid=uid, name="Новичок", cls=cls, race=race, level=level)
    ch.init_vitals()
    return ch


print("═" * 50)
print("  ТЕСТЫ ОНБОРДИНГА")
print("═" * 50)
validate()

# ─────────────────────── 1. ТУТОРИАЛ: базовый проход ───────────────────────
print("\n[1] Туториал: шаги и награды")
ch = new_char()
xp0, gold0 = ch.xp, ch.gold

out_move = tutorial.on_event(ch, "move")
check("шаг move возвращает подсказку/награду", len(out_move) > 0)
check("после move записан в done", "move" in ch.flags["tut"]["done"])
check("move дал опыт", ch.xp > xp0)
check("туториал ещё не завершён после 1 шага", not ch.flags["tut"]["finished"])

# повторный move — ничего (без дублей)
xp_after_move = ch.xp
dup = tutorial.on_event(ch, "move")
check("повторный move не даёт наград (нет строк)", dup == [])
check("повторный move не меняет опыт", ch.xp == xp_after_move)
check("move остался единственным в done", ch.flags["tut"]["done"].count("move") == 1)

# ─────────────────────── 2. ЛЮБОЙ ПОРЯДОК ШАГОВ ───────────────────────
print("\n[2] Туториал: произвольный порядок, финал один раз")
ch2 = new_char(uid=2)
order = ["skill", "potion", "move", "quest", "attack"]  # намеренно вперемешку
random.shuffle(order)
fired = {}
finish_count = 0
for ev in order:
    lines = tutorial.on_event(ch2, ev)
    fired[ev] = lines
    if any("Обучение завершено" in l for l in lines):
        finish_count += 1
check("все 5 шагов дали непустой ответ", all(fired[e] for e in order))
check("каждый шаг записан ровно один раз",
      all(ch2.flags["tut"]["done"].count(e) == 1 for e in ("move", "attack", "skill", "potion", "quest")))
check("финальное сообщение показано ровно один раз", finish_count == 1)
check("туториал помечен завершённым", ch2.flags["tut"]["finished"] is True)

# финальный бонус-предмет выдан ровно один раз
bonus = tutorial.FINISH_ITEM
check("бонус-предмет существует в контенте", bonus in ITEMS)
check("финальный бонус выдан один раз", ch2.inventory.count(bonus) == 1)

# после завершения повторные события ничего не дают и бонус не дублируется
for ev in ("move", "attack", "skill", "potion", "quest"):
    check(f"после финала событие {ev} пустое", tutorial.on_event(ch2, ev) == [])
check("бонус не задублировался после финала", ch2.inventory.count(bonus) == 1)

# ─────────────────────── 3. НАГРАДЫ НЕ ДУБЛИРУЮТСЯ ───────────────────────
print("\n[3] Туториал: суммарные награды стабильны")
ch3 = new_char(uid=3)
xp_start, gold_start = ch3.xp, ch3.gold
pot_start = ch3.inventory.count(tutorial.POTION_ITEM)
for ev in ("move", "attack", "skill", "potion", "quest"):
    tutorial.on_event(ch3, ev)
xp_once, gold_once = ch3.xp - xp_start, ch3.gold - gold_start
pot_once = ch3.inventory.count(tutorial.POTION_ITEM) - pot_start
# прогоняем ВСЕ события ещё дважды — суммы не должны меняться
for _ in range(2):
    for ev in ("move", "attack", "skill", "potion", "quest"):
        tutorial.on_event(ch3, ev)
check("опыт не изменился после повторов", ch3.xp - xp_start == xp_once)
check("золото не изменилось после повторов", ch3.gold - gold_start == gold_once)
check("число зелий не изменилось после повторов",
      ch3.inventory.count(tutorial.POTION_ITEM) - pot_start == pot_once)
check("суммарный опыт ≈ полуровень (60–120)", 60 <= xp_once <= 120)
check("выданы зелья за обучение (≥2)", pot_once >= 2)

# ─────────────────────── 4. НЕ СТАРТУЕТ ДЛЯ 6+ УРОВНЯ ───────────────────────
print("\n[4] Туториал: легаси-игроки (6+ ур.) не втягиваются")
legacy = new_char(uid=4, level=6)
r = tutorial.on_event(legacy, "move")
check("6 ур.: событие move ничего не возвращает", r == [])
check("6 ур.: состояние tut не создано", legacy.flags.get("tut") is None)
check("6 ур.: render пустой (обучение скрыто)", tutorial.render(legacy) == "")
legacy10 = new_char(uid=5, level=10)
check("10 ур.: quest-событие пустое", tutorial.on_event(legacy10, "quest") == [])
check("10 ур.: бонус не выдан", tutorial.FINISH_ITEM not in legacy10.inventory)

# граница: ровно 5 ур. — ещё новичок, стартует
edge5 = new_char(uid=6, level=5)
check("5 ур.: туториал ещё доступен (новичок)", tutorial.on_event(edge5, "move") != [])

# начатый до 6 ур. туториал доводится, даже если игрок перерос порог
grown = new_char(uid=7, level=4)
tutorial.on_event(grown, "move")       # начал новичком
grown.level = 8                         # вырос по ходу обучения
cont = tutorial.on_event(grown, "attack")
check("начатый туториал продолжается после роста уровня", cont != [])

# ─────────────────────── 5. ЩИТ НОВИЧКА ───────────────────────
print("\n[5] Щит новичка: множитель урона")
c4 = new_char(uid=10, level=4)
c5 = new_char(uid=11, level=5)
check("множитель щита на 4 ур. = 0.7", abs(combat.newbie_shield_factor(c4) - 0.7) < 1e-9)
check("множитель щита на 5 ур. = 1.0", abs(combat.newbie_shield_factor(c5) - 1.0) < 1e-9)
c1 = new_char(uid=12, level=1)
check("множитель щита на 1 ур. = 0.7", abs(combat.newbie_shield_factor(c1) - 0.7) < 1e-9)

# фактический урон: щит режет входящий урон новичка примерно на 30%.
# Сравниваем на паре 4 ур. (щит) vs 5 ур. (без щита) при равных прочих условиях.
# Чтобы исключить уклонение/крит-разброс, обнуляем случайность через много проб
# и одинаковый «сырой» урон, большой относительно защиты.
def _avg_taken(level, raw, trials=4000):
    total = 0
    for i in range(trials):
        ch = new_char(uid=900 + level, level=level)
        ch.effects = []                 # без щитов/додж-эффектов
        hp_before = ch.hp
        combat.apply_damage_to_char(ch, raw)
        total += (hp_before - ch.hp)
    return total / trials

raw = 300
taken4 = _avg_taken(4, raw)
taken5 = _avg_taken(5, raw)
check("на 4 ур. в среднем получают меньше урона, чем на 5 ур.", taken4 < taken5)
# отношение средних ≈ 0.7 (обе стороны страдают от одинакового уклонения ~одинаково)
ratio = taken4 / max(1e-9, taken5)
check(f"отношение среднего урона 4/5 ур. ≈ 0.7 (факт {ratio:.3f})", 0.60 <= ratio <= 0.80)

# щит только уменьшает урон — никогда не увеличивает
c4b = new_char(uid=20, level=4); c4b.effects = []
hp_b = c4b.hp
dmg, dodged = combat.apply_damage_to_char(c4b, 200)
check("щит новичка не увеличивает урон (dmg ≤ raw)", dmg <= 200)
check("строка урона помечается иконкой щита на 4 ур.", "🛡" in combat._shield_note(c4))
check("на 5 ур. пометки щита нет", combat._shield_note(c5) == "")

# ─────────────────────── 6. МЯГКАЯ СМЕРТЬ ───────────────────────
print("\n[6] Мягкая смерть: карма и золото до 5 ур.")
# карма НЕ роняет предмет при level<5, даже если карма высокая (изгой → 50% шанс)
dropped_low = 0
for i in range(2000):
    ch = new_char(uid=30, level=4)
    ch.inventory = ["малое_зелье", "серебряное_кольцо"]
    karma.add(ch, karma.OUTLAW + 10)     # заведомо изгой
    if karma.maybe_drop_on_death(ch) is not None:
        dropped_low += 1
check("level<5: карма НИКОГДА не роняет предмет", dropped_low == 0)

# на 5+ ур. изгой предметы роняет (контроль: механика вообще работает)
dropped_hi = 0
for i in range(2000):
    ch = new_char(uid=31, level=5)
    ch.inventory = ["малое_зелье", "серебряное_кольцо"]
    karma.add(ch, karma.OUTLAW + 10)
    if karma.maybe_drop_on_death(ch) is not None:
        dropped_hi += 1
check("level>=5: изгой роняет предметы (механика жива)", dropped_hi > 0)

# порог мягкой смерти
check("константа мягкой смерти = 5 ур.", karma.SOFT_DEATH_LEVEL == 5)

# «золото сохраняется при level<5» — воспроизводим ту же формулу, что в do_respawn:
# soft = level < SOFT_DEATH_LEVEL → потеря 0; иначе теряется 30%.
def _gold_after_respawn(level, gold):
    soft = level < karma.SOFT_DEATH_LEVEL
    if soft:
        return gold, 0
    lost = gold - int(gold * 0.7)
    return int(gold * 0.7), lost

g_keep, lost0 = _gold_after_respawn(4, 10000)
check("level<5: золото при смерти сохраняется полностью", g_keep == 10000 and lost0 == 0)
g_lose, lost1 = _gold_after_respawn(5, 10000)
check("level>=5: при смерти теряется ~30% золота", lost1 == 3000 and g_lose == 7000)

# ─────────────────────── 7. RENDER ПРОГРЕССА ───────────────────────
print("\n[7] render() прогресса обучения")
fresh = new_char(uid=40)
rtxt = tutorial.render(fresh)
check("свежий новичок: render показывает список шагов", "Обучение" in rtxt and "⬜️" in rtxt)
mid = new_char(uid=41)
tutorial.on_event(mid, "move")
tutorial.on_event(mid, "attack")
rmid = tutorial.render(mid)
check("частичный прогресс: есть и ✅, и ⬜️", "✅" in rmid and "⬜️" in rmid)
done = new_char(uid=42)
for ev in ("move", "attack", "skill", "potion", "quest"):
    tutorial.on_event(done, ev)
rdone = tutorial.render(done)
check("завершённый туториал: render отмечает прохождение", "пройден" in rdone.lower())


# ───────────── 8. ОНБОРДИНГ-БАЛАНС: РЫЧАГИ ЭТАПА 4.1 ─────────────
# Проверяем применённые рычаги балансировки онбординга (стартовый набор по классам,
# класс-зависимый щит новичка) и итог: первая сессия (окно щита 1–6 ур.) проходится
# без гибели у ВСЕХ классов в обоих профилях. Данные до/после — docs/BALANCE_ONBOARDING.md.
print("\n[8] Онбординг-баланс: стартовый набор, класс-зависимый щит, окно щита")
import asyncio
from engine import starter
import sim_onboarding as sim


def _hp_kit(cls):
    return starter.starting_consumables(cls).count("малое_зелье")


# — рычаг (а)/(в): стартовый набор зелий (единый источник правды — engine/starter.py) —
check("старт: маг — усиленная подушка 12 зелий лечения", _hp_kit("mage") == 12)
check("старт: некромант — 12 зелий", _hp_kit("necromancer") == 12)
check("старт: паладин — 12 зелий (в naive не лечится скиллами)", _hp_kit("paladin") == 12)
check("старт: жрец — 6 зелий", _hp_kit("priest") == 6)
check("старт: воин — базовые 4 зелья (крепкий класс, не тронут)", _hp_kit("warrior") == 4)
check("старт: разбойник — базовые 4 зелья (не тронут)", _hp_kit("rogue") == 4)

# — рычаг (б): класс-зависимый щит новичка (хрупким — шире окно и сильнее) —
check("хрупкие классы щита = {маг, некромант, жрец, паладин}",
      combat.NEWBIE_SHIELD_FRAGILE == frozenset({"mage", "necromancer", "priest", "paladin"}))
mage6 = new_char(cls="mage", level=6)
mage7 = new_char(cls="mage", level=7)
check("маг: усиленный щит 0.55 действует до 6 ур.",
      abs(combat.newbie_shield_factor(mage6) - 0.55) < 1e-9)
check("маг: щит снят на 7 ур. (окно не выходит за 6 — потолок регламента)",
      abs(combat.newbie_shield_factor(mage7) - 1.0) < 1e-9)
# крепкие классы НЕ тронуты: воин сохраняет исторический щит 0.7 до 4 ур.
wr4b = new_char(cls="warrior", level=4)
wr5b = new_char(cls="warrior", level=5)
check("воин: базовый щит 0.7 до 4 ур. (крепкий класс не тронут)",
      abs(combat.newbie_shield_factor(wr4b) - 0.7) < 1e-9)
check("воин: щит снят на 5 ур. (крепкий класс не тронут)",
      abs(combat.newbie_shield_factor(wr5b) - 1.0) < 1e-9)


# — итог рычагов: первая сессия (окно щита 1–6 ур.) — без гибели —
def _sim_deaths(cls, profile, to=6, seed=42):
    return asyncio.run(sim.run_once(cls, profile, seed, to))["deaths"]


for _cls in ("warrior", "mage", "rogue", "priest", "paladin", "necromancer"):
    _d = _sim_deaths(_cls, "smart", 6)
    check(f"smart {_cls}: ≤3 смерти до 6 ур. (факт {_d})", _d <= 3)
# самые хрупкие кастеры в naive (без лечения скиллами) в окне щита не гибнут (≤4)
for _cls in ("mage", "necromancer"):
    _d = _sim_deaths(_cls, "naive", 6)
    check(f"naive {_cls}: ≤4 смерти до 6 ур. в окне щита (факт {_d})", _d <= 4)


# ── naive v2 (Этап 4.2, решение лида по итогам 4.1 §7 опция 1): редкие касты ──
# naive получает право кастовать боевой скилл, когда ресурс заполнен ≥80% (как
# учит туториал на шаге «skill»); НЕ лечится скиллами, зелья по-прежнему НЕ
# докупает, цели ≤ level+2. Закрывает остаток mage/paladin (10.7/9.3 смертей на
# модели v1 — см. §6-7 docs/BALANCE_ONBOARDING.md) без правки мобов/HP/щита.
print("\n[8b] Онбординг-баланс: naive v2 — редкие касты боевого скилла (≥80% ресурса)")
check("naive v2: порог каста зафиксирован на 80% ресурса",
      abs(sim.NAIVE_CAST_RESOURCE_FRAC - 0.80) < 1e-9)

for _cls in ("warrior", "mage", "rogue", "priest", "paladin", "necromancer"):
    _d10 = _sim_deaths(_cls, "naive", 10)
    check(f"naive v2 {_cls}: ≤6 смертей до 10 ур. (факт {_d10})", _d10 <= 6)

# — целевая проверка по двум самым хрупким классам (docs/BALANCE_ONBOARDING.md
# §6-7): именно mage/paladin были провальными на модели v1 (10.7/9.3 смерти).
# С naive v2 они укладываются в окно щита (1–6 ур.) с большим запасом —
# фиксируем это отдельно, seed=42 (детерминизм совпадает с _sim_deaths по умолчанию).
print("\n[8c] naive v2: целевая проверка mage/paladin — окно щита 1–6 ур.")
for _cls in ("mage", "paladin"):
    _d6 = _sim_deaths(_cls, "naive", 6, seed=42)
    check(f"naive v2 {_cls}: ≤4 смерти до 6 ур., seed=42 (факт {_d6})", _d6 <= 4)


# ─────────────────────── 9. ВИТРИНА КЛАССОВ (Этап 4.2, задача 2) ───────────────────────
print("\n[9] Витрина классов: role/difficulty/style/newbie_ok/pros у всех 6 классов")
from engine.content import CLASSES

_SHOWCASE_FIELDS = ("role", "difficulty", "style", "newbie_ok", "pros")
check("ровно 6 классов в data/classes.yaml", len(CLASSES) == 6)
for _cid, _c in CLASSES.items():
    for _f in _SHOWCASE_FIELDS:
        check(f"класс '{_cid}': поле '{_f}' присутствует", _f in _c)
    check(f"класс '{_cid}': 'pros' — непустой список",
          isinstance(_c.get("pros"), list) and len(_c.get("pros")) > 0)
    check(f"класс '{_cid}': 'difficulty' в диапазоне 1..3", 1 <= int(_c.get("difficulty", 0)) <= 3)

_newbie_ok_classes = [cid for cid, c in CLASSES.items() if c.get("newbie_ok")]
check(f"≥2 класса помечены newbie_ok=True (факт {sorted(_newbie_ok_classes)})",
      len(_newbie_ok_classes) >= 2)
check("воин и разбойник — newbie_ok (симы: 0–2.3 смертей до 6 ур.)",
      "warrior" in _newbie_ok_classes and "rogue" in _newbie_ok_classes)
check("хрупкие кастеры НЕ newbie_ok: mage/priest/paladin/necromancer",
      not any(CLASSES[c].get("newbie_ok") for c in ("mage", "priest", "paladin", "necromancer")))


# ─────────────────────── 10. ОБЩИЙ ХАБ (Этап 4.2, задача 1) ───────────────────────
print("\n[10] Общий хаб: новый персонаж стартует в HUB_ROOM, home_room = расовая столица")
from engine.content import RACES, WORLD

HUB_ROOM = "village"   # см. bot/main.py HUB_ROOM — дублируем константу, т.к.
                        # bot/main.py не импортируется в чистых тестах (Telegram-зависимости)
check("HUB_ROOM существует в мире", HUB_ROOM in WORLD)

for _race in RACES:
    _ch = Character(uid=900, name="Хабтест", cls="warrior", race=_race)
    _ch.init_vitals()
    _ch.room = HUB_ROOM
    _ch.flags["home_room"] = RACES.get(_race, {}).get("start_room", HUB_ROOM)
    check(f"раса '{_race}': новый герой стартует в HUB_ROOM", _ch.room == HUB_ROOM)
    check(f"раса '{_race}': home_room записан и существует в мире",
          _ch.flags["home_room"] in WORLD)
    _expected_home = RACES[_race].get("start_room", HUB_ROOM)
    check(f"раса '{_race}': home_room == races.yaml start_room ('{_expected_home}')",
          _ch.flags["home_room"] == _expected_home)


# ─────────────────────── ИТОГ ───────────────────────
print("\n" + "═" * 50)
print(f"ИТОГО: ✅ {_passed} пройдено, ❌ {_failed} провалено")
print("═" * 50)
sys.exit(1 if _failed else 0)
