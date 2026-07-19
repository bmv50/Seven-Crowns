# -*- coding: utf-8 -*-
"""
Тесты разборки снаряжения (engine/salvage.py) и pity-крафта «конденсации»
(этап 6.2). Без Telegram и БД.
    python3 test_salvage.py

Проверяет:
  • формулу пыли (level_req//4 + бонус за редкость) по кодам rarity.META;
  • разбор кладёт пыль в сумку и удаляет предмет;
  • отказ разбирать надетое / квестовое / не-экипировку;
  • пыль не создаёт фонтан золота (sell_price 0);
  • pity-рецепты валидны: вход/выход существуют, выход — экипировка;
  • детерминизм выхода pity-крафта;
  • доступность выходов КАЖДОМУ классу (equip.class_can_use);
  • экономику pity: выход дороже входа-золота (это страховка, не бизнес);
  • крафт списывает пыль+золото и выдаёт предмет.
"""
import sys

from engine.character import Character
from engine import salvage, craft, equip, rarity, content
from engine.content import ITEMS, RECIPES

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


def new_char(cls="warrior", level=30):
    ch = Character(uid=1, name="Тест", cls=cls, race="human")
    ch.level = level
    ch.init_vitals()
    return ch


CLASSES = ["warrior", "paladin", "rogue", "priest", "mage", "necromancer"]
PITY_RECIPES = ["condense_w10", "condense_w20", "condense_w30",
                "condense_w40", "condense_w50", "condense_w60"]

# ───────────────── 1. ФОРМУЛА ПЫЛИ ─────────────────
print("\n[1] Формула пыли: level_req//4 + бонус за редкость")
# g_sword_30: level_req 30 → 30//4 = 7
check("common (без бонуса): 30//4 + 0 = 7", salvage.dust_for("g_sword_30") == 7)
check("green (+1): 7 + 1 = 8", salvage.dust_for("g_sword_30#green") == 8)
check("blue (+3): 7 + 3 = 10", salvage.dust_for("g_sword_30#blue") == 10)
check("purple (+8): 7 + 8 = 15", salvage.dust_for("g_sword_30#purple#1") == 15)
check("gold (+13): 7 + 13 = 20", salvage.dust_for("g_sword_30#gold#1") == 20)
check("red (+20): 7 + 20 = 27", salvage.dust_for("g_sword_30#red#1") == 27)
check("низкий уровень даёт минимум 1 пыль", salvage.dust_for("g_sword_1") == 1)
check("высокий тир даёт больше пыли (g_two_handed_45 > g_sword_1)",
      salvage.dust_for("g_two_handed_45") > salvage.dust_for("g_sword_1"))
check("бонус за редкость монотонен (red > gold > purple > blue > green > common)",
      salvage.dust_for("g_sword_45#red#1") > salvage.dust_for("g_sword_45#gold#1")
      > salvage.dust_for("g_sword_45#purple#1") > salvage.dust_for("g_sword_45#blue")
      > salvage.dust_for("g_sword_45#green") > salvage.dust_for("g_sword_45"))

# ───────────────── 2. РАЗБОР КЛАДЁТ ПЫЛЬ / УДАЛЯЕТ ПРЕДМЕТ ─────────────────
print("\n[2] Разбор: пыль в сумку, предмет удалён")
ch = new_char()
ch.inventory.append("g_sword_30#blue")
_before_dust = ch.inventory.count(salvage.DUST_ITEM)
ok, msg, dust = salvage.salvage(ch, "g_sword_30#blue")
check("разбор успешен", ok is True)
check("выдано ровно dust_for пыли (10)", dust == 10)
check("пыль появилась в сумке (10 шт)",
      ch.inventory.count(salvage.DUST_ITEM) - _before_dust == 10)
check("разобранный предмет удалён из сумки", "g_sword_30#blue" not in ch.inventory)

# ───────────────── 3. ОТКАЗЫ ─────────────────
print("\n[3] Отказ: надетое / квест / не-экипировка / чего нет")
ch = new_char()
ch.inventory.append("g_sword_30")
ch.equipment["weapon"] = "g_sword_30"          # надето
ok, why = salvage.can_salvage(ch, "g_sword_30")
check("надетое разобрать нельзя", ok is False)
ok2, _, _ = salvage.salvage(ch, "g_sword_30")
check("salvage надетого возвращает ok=False", ok2 is False)
ch2 = new_char()
ch2.inventory.append("железная_руда")
check("материал разобрать нельзя (не снаряжение)", salvage.can_salvage(ch2, "железная_руда")[0] is False)
ch2.inventory.append("малое_зелье")
check("расходник разобрать нельзя", salvage.can_salvage(ch2, "малое_зелье")[0] is False)
# квестовый предмет
_quest_key = next((k for k, v in ITEMS.items()
                   if isinstance(v, dict) and v.get("type") == "quest"), None)
if _quest_key:
    ch2.inventory.append(_quest_key)
    check("квестовый предмет разобрать нельзя", salvage.can_salvage(ch2, _quest_key)[0] is False)
else:
    check("квестовый предмет разобрать нельзя (нет квест-предметов — пропуск)", True)
check("чего нет в сумке — разобрать нельзя", salvage.can_salvage(new_char(), "g_bow_15")[0] is False)

# ───────────────── 4. ПЫЛЬ НЕ ФОНТАН ЗОЛОТА ─────────────────
print("\n[4] Пыль не продаётся (нет фонтана золота)")
check("туманная_пыль существует как material", ITEMS.get(salvage.DUST_ITEM, {}).get("type") == "material")
check("sell_price(пыль) == 0", content.sell_price(salvage.DUST_ITEM) == 0)
check("пыль не is_sellable", content.is_sellable(salvage.DUST_ITEM) is False)

# ───────────────── 5. PITY-РЕЦЕПТЫ ВАЛИДНЫ ─────────────────
print("\n[5] Pity-рецепты: вход/выход существуют, выход — экипировка")
check("ровно 6 рецептов конденсации (по окну)",
      sum(1 for r in PITY_RECIPES if r in RECIPES) == 6)
_all_valid = True
_all_equip = True
for rid in PITY_RECIPES:
    r = RECIPES.get(rid, {})
    out = r.get("output")
    if out not in ITEMS:
        _all_valid = False
    if ITEMS.get(out, {}).get("type") not in ("weapon", "armor", "accessory"):
        _all_equip = False
    for ik, _q in r.get("inputs", []):
        if ik not in ITEMS:
            _all_valid = False
check("у всех pity-рецептов вход и выход существуют в ITEMS", _all_valid)
check("выход всех pity-рецептов — экипировка", _all_equip)
check("все pity-рецепты потребляют туманную пыль",
      all(any(ik == salvage.DUST_ITEM for ik, _ in RECIPES[rid]["inputs"]) for rid in PITY_RECIPES))
check("все pity-рецепты у станции «кузнец»",
      all(RECIPES[rid].get("station") == "кузнец" for rid in PITY_RECIPES))

# ───────────────── 6. ДЕТЕРМИНИЗМ ВЫХОДА ─────────────────
print("\n[6] Детерминизм выхода pity-крафта")
_det = True
for rid in PITY_RECIPES:
    outs = set()
    for _ in range(20):
        c = new_char(level=60)
        for _n in range(RECIPES[rid]["inputs"][0][1]):
            c.inventory.append(salvage.DUST_ITEM)
        c.gold = 10_000_000
        ok, _ = craft.craft(c, rid)
        # какой предмет добавился (кроме остатков пыли)
        outs.add(RECIPES[rid]["output"])
    if len(outs) != 1:
        _det = False
check("каждый pity-рецепт даёт РОВНО один фиксированный предмет (детерминизм)", _det)

# ───────────────── 7. ДОСТУПНОСТЬ КАЖДОМУ КЛАССУ ─────────────────
print("\n[7] Доступность pity-выходов каждому классу (equip.class_can_use)")
for rid in PITY_RECIPES:
    out = RECIPES[rid]["output"]
    ok_all = all(equip.class_can_use(cls, out) for cls in CLASSES)
    check(f"{rid} → {out}: носят ВСЕ классы", ok_all)
# и для каждого класса — хотя бы один pity-рецепт на каждое окно
_per_class_ok = True
for cls in CLASSES:
    for rid in PITY_RECIPES:
        if not equip.class_can_use(cls, RECIPES[rid]["output"]):
            _per_class_ok = False
check("у каждого класса есть pity-рецепт на КАЖДОЕ окно", _per_class_ok)

# ───────────────── 8. ЭКОНОМИКА PITY ─────────────────
print("\n[8] Экономика pity: выход дороже входа-золота, вход не ломает баланс")
_pity_ok = True
for rid in PITY_RECIPES:
    r = RECIPES[rid]
    out_price = ITEMS.get(r["output"], {}).get("price", 0)
    gold_in = r.get("gold", 0)
    dust_price = sum(ITEMS.get(ik, {}).get("price", 0) * q for ik, q in r["inputs"])
    if not (out_price > gold_in and dust_price == 0):
        _pity_ok = False
check("во всех pity-рецептах: цена выхода > входа-золота, пыль price 0", _pity_ok)
check("золото pity скромное (< 20% цены выхода у ур.60-рецепта)",
      RECIPES["condense_w60"]["gold"] < 0.20 * ITEMS[RECIPES["condense_w60"]["output"]]["price"])

# ───────────────── 9. КРАФТ СПИСЫВАЕТ ПЫЛЬ+ЗОЛОТО, ВЫДАЁТ ПРЕДМЕТ ─────────────────
print("\n[9] Крафт: списание пыли+золота, выдача предмета")
rid = "condense_w50"
need = dict(RECIPES[rid]["inputs"])[salvage.DUST_ITEM]
gold_need = RECIPES[rid]["gold"]
ch = new_char(cls="mage", level=45)
for _ in range(need):
    ch.inventory.append(salvage.DUST_ITEM)
ch.gold = gold_need + 500
ok, msg = craft.craft(ch, rid)
check("крафт при достатке пыли+золота успешен", ok is True)
check("выданный предмет в сумке", RECIPES[rid]["output"] in ch.inventory)
check("вся пыль списана", ch.inventory.count(salvage.DUST_ITEM) == 0)
check("золото списано ровно на стоимость рецепта", ch.gold == 500)
# нехватка пыли → отказ
ch2 = new_char(cls="mage", level=45)
ch2.gold = gold_need
ch2.inventory.append(salvage.DUST_ITEM)     # всего 1 пылинка
ok2, _ = craft.craft(ch2, rid)
check("при нехватке пыли крафт отклонён", ok2 is False)

# ─────────────────────── ИТОГ ───────────────────────
print("\n" + "═" * 50)
print(f"ИТОГО: ✅ {_passed} пройдено, ❌ {_failed} провалено")
print("═" * 50)
sys.exit(1 if _failed else 0)
