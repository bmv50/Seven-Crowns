# -*- coding: utf-8 -*-
"""
Тесты расширенного движка квестов (этап 5.1): новые типы целей
(talk / reach / use / choose) и механика последствий (exclusive_group,
locks, on_complete: флаги/репутация/хроника), а также валидатор.

Запуск:  python test_quest_types.py
Без Telegram и без PostgreSQL — чистый движок.
"""
import sys

from engine.content import validate, QUESTS, FACTIONS
from engine.character import Character
from engine import quest, reputation, chronicle

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


def new_char(uid=1, name="Тест", room="village"):
    ch = Character(uid=uid, name=name, cls="warrior", race="human", room=room)
    return ch


# ─────────────────────── 1. TALK ───────────────────────
print("\n[1] Цель talk — разговор с NPC")
c = new_char(uid=1)
ok, _ = quest.accept(c, "sample_talk_elder")
check("talk-квест принят", ok and c.quests.get("sample_talk_elder") == "active")
check("talk-цель не выполнена до разговора", not quest.is_complete(c, "sample_talk_elder"))
msgs = quest.on_talk(c, "жрец_храма")
check("on_talk с нужным NPC даёт сообщение", len(msgs) == 1)
check("флаг прогресса talk выставлен", c.quests.get("sample_talk_elder:talk") == "1")
check("talk-цель выполнена после разговора", quest.is_complete(c, "sample_talk_elder"))
check("посторонний NPC не влияет", quest.on_talk(new_char(uid=99), "кузнец") == [])
ok, _ = quest.complete(c, "sample_talk_elder")
check("talk-квест сдан", ok and c.quests.get("sample_talk_elder") == "done")
check("служебный ключ talk очищен при сдаче", "sample_talk_elder:talk" not in c.quests)


# ─────────────────────── 2. REACH ───────────────────────
print("\n[2] Цель reach — достичь комнаты")
c = new_char(uid=2)
quest.accept(c, "sample_reach_well")
check("вход в чужую комнату не засчитывает", quest.on_enter_room(c, "forest_edge") == [])
check("reach не выполнен", not quest.is_complete(c, "sample_reach_well"))
msgs = quest.on_enter_room(c, "well")
check("вход в целевую комнату даёт сообщение", len(msgs) == 1)
check("reach-цель выполнена", quest.is_complete(c, "sample_reach_well"))
ok, _ = quest.complete(c, "sample_reach_well")
check("reach-квест сдан", ok and c.quests.get("sample_reach_well") == "done")


# ─────────────────────── 3. USE ───────────────────────
print("\n[3] Цель use — использовать предмет")
c = new_char(uid=3)
quest.accept(c, "sample_use_potion")
msgs = quest.on_use_item(c, "малое_зелье")
check("использование нужного предмета даёт сообщение", len(msgs) == 1)
check("use-цель выполнена", quest.is_complete(c, "sample_use_potion"))
check("повторное использование идемпотентно", quest.on_use_item(c, "малое_зелье") == [])
check("чужой предмет не засчитывается", quest.on_use_item(c, "факел") == [])
ok, _ = quest.complete(c, "sample_use_potion")
check("use-квест сдан", ok and c.quests.get("sample_use_potion") == "done")


# ─────────────────────── 4. CHOOSE + on_complete ───────────────────────
print("\n[4] Цель choose — выбор игрока и последствия")
chronicle.reset()
c = new_char(uid=4, name="Герой")
quest.accept(c, "sample_choose_faith")
pend = quest.pending_choices(c, "жрец_храма")
check("choose-квест предлагает выбор у жреца", any(q == "sample_choose_faith" for q, _ in pend))
check("choose-цель не выполнена без выбора", not quest.is_complete(c, "sample_choose_faith"))
ok, _ = quest.on_choose(c, "sample_choose_faith", "light")
check("выбор зафиксирован", ok and quest.choice_made(c, "sample_choose_faith") == "light")
check("выбор записан в ch.flags['quest_choices']",
      c.flags.get("quest_choices", {}).get("sample_choose_faith") == "light")
check("choose-цель выполнена после выбора", quest.is_complete(c, "sample_choose_faith"))
ok2, _ = quest.on_choose(c, "sample_choose_faith", "balance")
check("второй вариант недоступен после выбора", not ok2)
check("выбор остался прежним", quest.choice_made(c, "sample_choose_faith") == "light")
check("после выбора квест выбывает из pending", quest.pending_choices(c, "жрец_храма") == [])
rep0 = reputation.points(c, "orden_rassveta")
ok, _ = quest.complete(c, "sample_choose_faith")
check("choose-квест сдан", ok and c.quests.get("sample_choose_faith") == "done")
check("on_complete выставил флаг", c.flags.get("sample_faith") == "chosen")
check("on_complete применил репутацию", reputation.points(c, "orden_rassveta") == rep0 + 100)
check("on_complete записал хронику с именем игрока",
      any("Герой" in r for r in chronicle.recent(6)))


# ─────────────────────── 5. EXCLUSIVE_GROUP ───────────────────────
print("\n[5] Эксклюзив-группа: приём одного лочит остальных")
_base = {"turn_in": "старейшина", "desc": "тест",
         "objective": {"type": "talk", "npc": "жрец_храма"}, "reward": {"xp": 1}}
QUESTS["t_grp_a"] = dict(_base, name="Гр-А", giver="старейшина", exclusive_group="tgrp")
QUESTS["t_grp_b"] = dict(_base, name="Гр-Б", giver="старейшина", exclusive_group="tgrp")
try:
    c = new_char(uid=5)
    av0 = quest.available_quests(c, "старейшина")
    check("оба члена группы доступны изначально", "t_grp_a" in av0 and "t_grp_b" in av0)
    quest.accept(c, "t_grp_a")
    check("после приёма — второй член группы заблокирован", quest.is_locked(c, "t_grp_b"))
    av1 = quest.available_quests(c, "старейшина")
    check("заблокированный член не выдаётся", "t_grp_b" not in av1)
    okb, _ = quest.accept(c, "t_grp_b")
    check("принять заблокированный член нельзя", not okb)
finally:
    QUESTS.pop("t_grp_a", None)
    QUESTS.pop("t_grp_b", None)


# ─────────────────────── 6. LOCKS ПРИ ЗАВЕРШЕНИИ ───────────────────────
print("\n[6] locks: завершение квеста блокирует перечисленные")
QUESTS["t_lock_src"] = dict(_base, name="Ист", giver="старейшина", locks=["t_lock_tgt"])
QUESTS["t_lock_tgt"] = dict(_base, name="Цель", giver="старейшина")
try:
    c = new_char(uid=6)
    check("цель блокировки доступна до завершения источника",
          "t_lock_tgt" in quest.available_quests(c, "старейшина"))
    quest.accept(c, "t_lock_src")
    quest.on_talk(c, "жрец_храма")
    ok, _ = quest.complete(c, "t_lock_src")
    check("источник сдан", ok)
    check("после сдачи цель заблокирована", quest.is_locked(c, "t_lock_tgt"))
    check("заблокированная цель не выдаётся",
          "t_lock_tgt" not in quest.available_quests(c, "старейшина"))
finally:
    QUESTS.pop("t_lock_src", None)
    QUESTS.pop("t_lock_tgt", None)


# ─────────────────────── 7. ВАЛИДАТОР ───────────────────────
print("\n[7] Валидатор ловит битые ссылки новых типов")


def expect_invalid(qid, qdict, label):
    QUESTS[qid] = qdict
    try:
        validate()
        check(label, False)             # должно было упасть
    except ValueError:
        check(label, True)
    finally:
        QUESTS.pop(qid, None)


check("валидный контент проходит", (validate() or True))
_g = {"giver": "старейшина", "turn_in": "старейшина", "desc": "d", "reward": {"xp": 1}}
expect_invalid("bad_talk", dict(_g, name="x", objective={"type": "talk", "npc": "нет_такого"}),
               "talk с несуществующим NPC — ошибка")
expect_invalid("bad_reach", dict(_g, name="x", objective={"type": "reach", "room": "нет_комнаты"}),
               "reach с несуществующей комнатой — ошибка")
expect_invalid("bad_use", dict(_g, name="x", objective={"type": "use", "item": "нет_предмета"}),
               "use с несуществующим предметом — ошибка")
expect_invalid("bad_choose_empty", dict(_g, name="x", objective={"type": "choose", "options": []}),
               "choose без опций — ошибка")
expect_invalid("bad_choose_dup", dict(_g, name="x", objective={"type": "choose",
               "options": [{"id": "a", "label": "A"}, {"id": "a", "label": "B"}]}),
               "choose с дублирующимися id — ошибка")
expect_invalid("bad_lock", dict(_g, name="x", objective={"type": "talk", "npc": "жрец_храма"},
               locks=["нет_квеста"]), "locks на несуществующий квест — ошибка")
expect_invalid("bad_rep", dict(_g, name="x", objective={"type": "talk", "npc": "жрец_храма"},
               on_complete={"reputation": {"нет_фракции": 10}}),
               "on_complete.reputation с несуществующей фракцией — ошибка")
# эксклюзив-группа из одного члена — бессмысленна
QUESTS["lone_grp"] = dict(_g, name="x", objective={"type": "talk", "npc": "жрец_храма"},
                          exclusive_group="одиночка")
try:
    validate()
    check("эксклюзив-группа из 1 члена — ошибка", False)
except ValueError:
    check("эксклюзив-группа из 1 члена — ошибка", True)
finally:
    QUESTS.pop("lone_grp", None)
check("после инъекций контент снова валиден", (validate() or True))


# ═══════════════════════ КОНТЕНТ 5.2 ═══════════════════════
from engine.content import NPCS, WORLD, MOBS, ITEMS
from engine import bestiary as _best


# ── 8. Развилка Орден↔Ковен: полный цикл через движок ──
print("\n[8] Контент 5.2 — развилка Орден↔Ковен (полный цикл, взаимоисключение)")
_grp = set(quest._group_members("orden_koven"))
check("эксклюзив-группа orden_koven = 2 вступительных ветки",
      _grp == {"choice_holy_fire", "choice_pact_dead"})
c = new_char(uid=8, name="Клятвенник")
quest.accept(c, "choice_prologue")
for _ in range(3):
    quest.on_kill(c, "утопленник")
quest.complete(c, "choice_prologue")
quest.accept(c, "oath_crossroads")
quest.on_choose(c, "oath_crossroads", "orden")
quest.complete(c, "oath_crossroads")
_av = quest.available_quests(c, "паладин_наставник") + quest.available_quests(c, "болотная_знахарка")
check("после развилки доступны обе ветки", "choice_holy_fire" in _av and "choice_pact_dead" in _av)
quest.accept(c, "choice_holy_fire")
check("приём ветки Ордена лочит ветку Ковена", quest.is_locked(c, "choice_pact_dead"))
check("заблокированная ветка Ковена не выдаётся",
      "choice_pact_dead" not in quest.available_quests(c, "болотная_знахарка"))
_okk, _ = quest.accept(c, "choice_pact_dead")
check("принять ветку Ковена после Ордена нельзя", not _okk)
_ro0, _rk0 = reputation.points(c, "orden_rassveta"), reputation.points(c, "koven_gnilotopi")
for _ in range(4):
    quest.on_kill(c, "осквернённый_храмовник")
quest.complete(c, "choice_holy_fire")
check("on_complete ветки выставил флаг path=orden", c.flags.get("path") == "orden")
check("репутация Ордена выросла на 15", reputation.points(c, "orden_rassveta") == _ro0 + 15)
check("репутация Ковена упала на 10", reputation.points(c, "koven_gnilotopi") == _rk0 - 10)
quest.accept(c, "dawn_reckoning")
for _ in range(5):
    quest.on_kill(c, "осквернённый_храмовник")
quest.complete(c, "dawn_reckoning")
check("финал Ордена (locks) блокирует финал Ковена", quest.is_locked(c, "koven_covenant"))


# ── 9. Вертикаль «Тайна Колодца»: связность, полнота типов, валидность ──
print("\n[9] Контент 5.2 — вертикаль «Тайна Колодца»")
CRYPT = ["crypt_omen", "crypt_descent", "crypt_elder_lore", "crypt_restless_dead",
         "crypt_gather_ectoplasm", "crypt_ward", "crypt_choice",
         "crypt_pyre_rite", "crypt_vigil_rite"]
check("все квесты вертикали существуют", all(q in QUESTS for q in CRYPT))
_reqs = {q: QUESTS[q].get("requires") for q in CRYPT}
check("crypt_omen — корень цепочки (без requires)", _reqs["crypt_omen"] is None)
check("каждый шаг требует звено этой же цепочки",
      all(_reqs[q] in CRYPT for q in CRYPT if q != "crypt_omen"))
_types = {QUESTS[q]["objective"]["type"] for q in CRYPT}
check("вертикаль задействует все типы целей",
      {"talk", "reach", "kill", "collect", "use", "choose"} <= _types)


def _obj_ref_ok(q):
    o = QUESTS[q]["objective"]; t = o["type"]
    if t == "talk":   return o["npc"] in NPCS
    if t == "reach":  return o["room"] in WORLD
    if t == "kill":   return o["mob"] in MOBS
    if t in ("collect", "use"): return o["item"] in ITEMS
    if t == "choose": return len(o.get("options", [])) >= 2
    return False


check("все цели вертикали ссылаются на валидные id", all(_obj_ref_ok(q) for q in CRYPT))
check("финалы вертикали — эксклюзив-пара crypt_rite",
      QUESTS["crypt_pyre_rite"].get("exclusive_group") == "crypt_rite"
      and QUESTS["crypt_vigil_rite"].get("exclusive_group") == "crypt_rite")
check("погребальный_ладан — расходник (годен к use)",
      ITEMS.get("погребальный_ладан", {}).get("type") == "consumable")
c = new_char(uid=9)
quest.accept(c, "crypt_ward")
c.inventory.append("погребальный_ладан")
quest.on_use_item(c, "погребальный_ладан")
check("use-цель вертикали засчитана через движок", quest.is_complete(c, "crypt_ward"))


# ── 10. Мета-коллекция «Семь Корон» ──
print("\n[10] Контент 5.2 — коллекция «Семь Корон»")
_col = (_best.COLLECTIONS or {}).get("col_seven_crowns")
check("коллекция col_seven_crowns существует", _col is not None)
check("в коллекции ровно 7 венценосцев", bool(_col) and len(_col["mobs"]) == 7)
check("все 7 — существующие мобы", bool(_col) and all(m in MOBS for m in _col["mobs"]))
check("все 7 помечены boss: true", bool(_col) and all(MOBS[m].get("boss") is True for m in _col["mobs"]))
check("у коллекции есть титул и крупная награда-золото",
      bool(_col) and _col.get("title") and _col["reward"].get("gold", 0) >= 40000)
check("предметы-награды коллекции существуют",
      bool(_col) and all(i in ITEMS for i in _col["reward"].get("items", [])))


# ─────────────────────── ИТОГ ───────────────────────
print(f"\nИТОГО: {_passed} прошло, {_failed} упало")
sys.exit(1 if _failed else 0)
