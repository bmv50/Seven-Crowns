# -*- coding: utf-8 -*-
"""
Автономные тесты ИИ-поручений (engine/errands.py + валидация ai/actions.py).
Запуск из каталога проекта:
    python3 test_errands.py

Без сети и без БД: LLM НЕ нужен — проверяется движковый путь (кандидаты,
формула награды, полный цикл kill/collect, лимиты) и валидация выбора модели
(индекс/длина текста). Телеграм и aiogram не импортируются.
"""
import random
import sys

from engine.character import Character
from engine import errands
from engine.content import MOBS, ITEMS
from ai import actions

random.seed(20240711)

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


def new_char(level=5, uid=1):
    ch = Character(uid=uid, name="Тест", cls="warrior", race="human")
    ch.init_vitals()
    ch.level = level
    ch.xp = 0
    ch.gold = 0
    return ch


NPC = "лесная_ведьма"          # стоит в лесной зоне, есть кандидаты на ур.5
NPC2 = "кузнец"                # другой NPC (для проверки «сдача только выдавшему»)


# ─────────────────── 1. КАНДИДАТЫ ───────────────────
print("\n[1] Кандидаты: границы уровня, не боссы, лимит, детерминизм")
ch = new_char(level=5)
cands = errands.candidates(ch, NPC)
check("кандидаты не пусты", len(cands) > 0)
check("не более MAX_CANDIDATES", len(cands) <= errands.MAX_CANDIDATES)
_lo, _hi = 5 - errands.LEVEL_LOW, 5 + errands.LEVEL_HIGH
check("уровни мобов в окне [level-2 .. level+3]",
      all(_lo <= c["mob_level"] <= _hi for c in cands))
check("нет боссов среди целей",
      all(not MOBS.get(c.get("mob") or c.get("from_mob"), {}).get("boss") for c in cands))
check("count kill в [4..8]",
      all(4 <= c["count"] <= 8 for c in cands if c["type"] == "kill"))
check("count collect в [3..6]",
      all(3 <= c["count"] <= 6 for c in cands if c["type"] == "collect"))
check("есть и kill, и collect типы",
      any(c["type"] == "kill" for c in cands) and any(c["type"] == "collect" for c in cands))
check("collect только из loot с шансом >= 0.2",
      all(any(e[0] == c["item"] and e[1] >= errands.MIN_LOOT_CHANCE
              for e in MOBS.get(c["from_mob"], {}).get("loot", []))
          for c in cands if c["type"] == "collect"))
check("детерминизм: два вызова дают идентичный список",
      errands.candidates(ch, NPC) == cands)
check("нет кандидатов для NPC без зоны/спавнов рядом (пустой список безопасен)",
      isinstance(errands.candidates(ch, "__нет_такого_npc__"), list))


# ─────────────────── 2. OFFER без выбора LLM ───────────────────
print("\n[2] offer() без choice — валидный кандидат + шаблонный текст")
off = errands.offer(ch, NPC)
check("offer без choice не None", off is not None)
check("offer содержит тип/count/reward/text/npc",
      all(k in off for k in ("type", "count", "reward", "text", "npc")))
check("offer.npc == выдавший", off["npc"] == NPC)
check("текст шаблона непустой", isinstance(off["text"], str) and len(off["text"]) > 0)
check("reward.xp и gold положительны",
      off["reward"]["xp"] > 0 and off["reward"]["gold"] > 0)


# ─────────────────── 3. НАГРАДА ПО ФОРМУЛЕ ───────────────────
print("\n[3] Награда в границах формулы (xp=N·xp·0.35, gold=N·gold·0.40)")
_kill = next(c for c in cands if c["type"] == "kill")
_off_k = errands.offer(ch, NPC, choice={"idx": cands.index(_kill), "text": "бей!"})
_m = MOBS[_kill["mob"]]
_exp_xp = max(1, round(_kill["count"] * _m["xp"] * errands.ERRAND_XP_MULT))
_exp_gold = max(1, round(_kill["count"] * _m["gold"] * errands.ERRAND_GOLD_MULT))
check("xp награды == формула", _off_k["reward"]["xp"] == _exp_xp)
check("gold награды == формула", _off_k["reward"]["gold"] == _exp_gold)
check("текст выбора LLM сохранён", _off_k["text"] == "бей!")
# порядок величин: срез относительно «сырой» ценности убийств
check("xp награды < N·xp_моба (срез, поручение повторяемо)",
      _off_k["reward"]["xp"] < _kill["count"] * _m["xp"])
check("gold награды < N·gold_моба (срез)",
      _off_k["reward"]["gold"] < _kill["count"] * _m["gold"])


# ─────────────────── 4. ЦИКЛ KILL ───────────────────
print("\n[4] Полный цикл KILL: accept → on_kill → turn_in")
ch = new_char(level=5)
cands = errands.candidates(ch, NPC)
_kill = next(c for c in cands if c["type"] == "kill")
off = errands.offer(ch, NPC, choice={"idx": cands.index(_kill), "text": "к делу"})
mob = off["mob"]; need = off["count"]
res = errands.accept(ch, off)
check("accept возвращает подтверждение", "Принято" in res)
check("активное поручение установлено", errands.has_active(ch))
check("taken_today == 1 после первого accept", errands.taken_today(ch) == 1)
check("on_kill по ЧУЖОМУ мобу игнорируется", errands.on_kill(ch, "__чужой_моб__") is None)
_last = None
for i in range(need):
    _last = errands.on_kill(ch, mob)
check("после N убийств есть уведомление о готовности",
      _last is not None and "выполнено" in _last)
check("прогресс достиг count", ch.flags["errand"]["progress"] >= need)
check("лишний on_kill после выполнения не увеличивает прогресс",
      errands.on_kill(ch, mob) is None and ch.flags["errand"]["progress"] == need)
check("can_turn_in True у выдавшего NPC", errands.can_turn_in(ch, NPC))
check("can_turn_in False у другого NPC (сдача только выдавшему)",
      not errands.can_turn_in(ch, NPC2))
_xp0, _gold0 = ch.xp, ch.gold
_msg = errands.turn_in(ch, NPC)
check("turn_in возвращает результат", _msg is not None and "сдано" in _msg)
check("xp начислен", ch.xp == _xp0 + off["reward"]["xp"])
check("gold начислен", ch.gold == _gold0 + off["reward"]["gold"])
check("активное поручение очищено после сдачи", not errands.has_active(ch))


# ─────────────────── 5. ЦИКЛ COLLECT ───────────────────
print("\n[5] Полный цикл COLLECT: предметы в инвентарь → turn_in списывает")
ch = new_char(level=5)
cands = errands.candidates(ch, NPC)
_col = next(c for c in cands if c["type"] == "collect")
off = errands.offer(ch, NPC, choice={"idx": cands.index(_col), "text": "принеси"})
item = off["item"]; need = off["count"]
errands.accept(ch, off)
check("can_turn_in False без предметов", not errands.can_turn_in(ch, NPC))
# кладём ровно нужное количество предметов руками
ch.inventory.extend([item] * need)
check("can_turn_in True когда предметов достаточно", errands.can_turn_in(ch, NPC))
_have0 = ch.inventory.count(item)
_msg = errands.turn_in(ch, NPC)
check("turn_in collect успешен", _msg is not None)
check("предметы списаны при сдаче", ch.inventory.count(item) == _have0 - need)
check("поручение очищено", not errands.has_active(ch))


# ─────────────────── 6. ОДНО АКТИВНОЕ ───────────────────
print("\n[6] Только одно активное поручение")
ch = new_char(level=5)
cands = errands.candidates(ch, NPC)
o1 = errands.offer(ch, NPC, choice={"idx": 0, "text": "раз"})
errands.accept(ch, o1)
o2 = errands.offer(ch, NPC, choice={"idx": 1, "text": "два"})
_r2 = errands.accept(ch, o2)
check("второй accept при активном отклонён", "уже есть активное" in _r2)
check("can_offer False при активном", not errands.can_offer(ch, NPC))
check("активным осталось первое поручение", ch.flags["errand"]["text"] == "раз")


# ─────────────────── 7. ЛИМИТ 3/ДЕНЬ ───────────────────
print("\n[7] Лимит 3 взятых поручения в день")
ch = new_char(level=5)
for i in range(errands.MAX_PER_DAY):
    o = errands.offer(ch, NPC, choice={"idx": 0, "text": f"n{i}"})
    errands.accept(ch, o)
    errands.abandon(ch)                     # освобождаем слот, счётчик НЕ откатывается
check("taken_today == MAX_PER_DAY после 3 взятий",
      errands.taken_today(ch) == errands.MAX_PER_DAY)
o4 = errands.offer(ch, NPC, choice={"idx": 0, "text": "четвёртое"})
_r4 = errands.accept(ch, o4)
check("4-й accept за день отклонён", "хватит" in _r4)
check("can_offer False при исчерпанном лимите", not errands.can_offer(ch, NPC))


# ─────────────────── 8. ABANDON ───────────────────
print("\n[8] Бросить поручение")
ch = new_char(level=5)
check("abandon без активного — вежливый отказ", "нет активного" in errands.abandon(ch))
o = errands.offer(ch, NPC, choice={"idx": 0, "text": "бросим"})
errands.accept(ch, o)
check("abandon активного очищает флаг",
      "брошено" in errands.abandon(ch) and not errands.has_active(ch))
check("render пуст без активного поручения", errands.render(ch) == "")


# ─────────────────── 9. RENDER ───────────────────
print("\n[9] render() для журнала")
ch = new_char(level=5)
o = errands.offer(ch, NPC, choice={"idx": 0, "text": "журнал"})
errands.accept(ch, o)
_rnd = errands.render(ch)
check("render непуст при активном", isinstance(_rnd, str) and len(_rnd) > 0)
check("render содержит блок Поручение и Награду",
      "Поручение" in _rnd and "Награда" in _rnd)


# ─────────────────── 10. ВАЛИДАЦИЯ ВЫБОРА LLM ───────────────────
print("\n[10] Валидация действия offer_errand (ai/actions.py)")
ch = new_char(level=5)
_n = len(errands.candidates(ch, NPC))
# корректный выбор
_ok = actions.validate({"action": "offer_errand", "idx": 0, "text": "Возьми дело"},
                       ch, NPC, {})
check("валидный offer_errand проходит", _ok is not None and _ok["action"] == "offer_errand")
check("валидатор нормализует idx/text", _ok["idx"] == 0 and _ok["text"] == "Возьми дело")
# плохой индекс
check("idx вне границ → None",
      actions.validate({"action": "offer_errand", "idx": _n + 99, "text": "x"}, ch, NPC, {}) is None)
check("отрицательный idx → None",
      actions.validate({"action": "offer_errand", "idx": -1, "text": "x"}, ch, NPC, {}) is None)
check("idx не число → None",
      actions.validate({"action": "offer_errand", "idx": "abc", "text": "x"}, ch, NPC, {}) is None)
# длинный текст обрезается до <=200
_long = actions.validate({"action": "offer_errand", "idx": 0, "text": "а" * 500}, ch, NPC, {})
check("длинный текст обрезан до <=200 символов",
      _long is not None and len(_long["text"]) <= 200)
# markdown-инъекции вычищаются
_inj = actions.validate({"action": "offer_errand", "idx": 0, "text": "во*от*_и_`код`[x]"}, ch, NPC, {})
check("markdown-символы вычищены из текста",
      _inj is not None and not any(s in _inj["text"] for s in "*_`[]"))
# при активном поручении offer_errand невалиден
errands.accept(ch, errands.offer(ch, NPC, choice={"idx": 0, "text": "занят"}))
check("offer_errand отклонён при активном поручении",
      actions.validate({"action": "offer_errand", "idx": 0, "text": "x"}, ch, NPC, {}) is None)
# parse хвостового JSON
_txt, _act = actions.parse('Здравствуй, путник. {"action":"offer_errand","idx":2,"text":"дело"}')
check("parse выделяет offer_errand из хвоста реплики",
      _act is not None and _act.get("action") == "offer_errand" and _act.get("idx") == 2)
check("parse отделяет текст реплики от JSON", _txt == "Здравствуй, путник.")

# offer с плохим idx возвращает None (движковая защита независимо от валидатора)
check("offer(choice bad idx) → None",
      errands.offer(new_char(), NPC, choice={"idx": 999, "text": "x"}) is None)


# ─────────────────────── ИТОГ ───────────────────────
print("\n" + "═" * 50)
print(f"ИТОГО: ✅ {_passed} пройдено, ❌ {_failed} провалено")
print("═" * 50)
sys.exit(1 if _failed else 0)
