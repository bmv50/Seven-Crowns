# -*- coding: utf-8 -*-
"""
UI-правки по отчёту тестировщика (пункты 3, 14, 17, 19, 20).
Запуск:
    python run_tests.py ui_fixes

Пункты и их суть:
  3  — продажа за меньше монеты не меняла ни одной цифры на экране: Telegram
       видел тот же текст и отказывался перерисовывать сообщение, игрок читал
       остаток от ПРЕДЫДУЩЕЙ продажи. Лечится полом цены в одну монету;
  14 — «Назад» с экрана достижений вёл в «Герой» независимо от точки входа;
  17 — характеристика класса тонула в художественном описании;
  19 — «Требуется уровень: N» без своего уровня рядом ни о чём не говорит;
  20 — «Отдохнувший опыт» игрок не понял (это запас удвоения, а не опыт).
"""
import sys

from bot import ui
from engine.content import ITEMS, sell_price, CLASSES
from engine.character import Character
from engine.money import COIN

_passed = 0
_failed = 0


def check(name, cond, extra=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✅ {name}")
    else:
        _failed += 1
        print(f"  ❌ {name}" + (f" — {extra}" if extra else ""))


def _hero(level=8):
    ch = Character(uid=1, name="Тест", cls="warrior", race="human")
    ch.level = level
    ch.init_vitals()
    return ch


# ───────────────── 3. цена продажи ─────────────────

def test_sell_price_visible():
    print("\n[3] Любая продажа заметна на экране")
    cheap = [(sell_price(k), k) for k in ITEMS
             if isinstance(ITEMS[k], dict) and "#" not in k and sell_price(k) > 0]
    below = [(p, k) for p, k in cheap if p < COIN]
    check("нет предметов дешевле одной монеты", not below,
          ", ".join(f"{k}={p}" for p, k in below[:5]))
    # именно эти три и ловил тестировщик
    for k in ("перо", "кость", "паучий_шёлк"):
        if k in ITEMS:
            check(f"{k}: продажа даёт хотя бы монету", sell_price(k) >= COIN,
                  sell_price(k))


# ───────────────── 14. возврат «Назад» ─────────────────

def test_titles_back_target():
    print("\n[14] «Назад» с достижений ведёт по месту входа")
    ch = _hero()
    kb = ui.kb_titles(ch)
    back = kb.inline_keyboard[-1][0]
    check("по умолчанию — в карточку героя", back.callback_data == "stats",
          back.callback_data)
    kb2 = ui.kb_titles(ch, back="titleshop")
    back2 = kb2.inline_keyboard[-1][0]
    check("из лавки титулов — обратно в лавку",
          back2.callback_data == "titleshop", back2.callback_data)
    # кнопка в лавке титулов должна передавать точку входа
    shop = ui.kb_title_shop(ch)
    titles_btn = [b for row in shop.inline_keyboard for b in row
                  if "итул" in b.text and (b.callback_data or "").startswith("achv")]
    check("кнопка «Мои титулы» передаёт точку входа",
          titles_btn and titles_btn[0].callback_data == "achv:titleshop",
          [b.callback_data for b in titles_btn])


# ───────────────── 17. характеристика класса ─────────────────

def test_class_card_primary():
    print("\n[17] Главная характеристика — отдельной строкой")
    for cid in CLASSES:
        card = ui.class_card(cid)
        check(f"{cid}: характеристика вынесена",
              "Главная характеристика" in card, card[:80])
    # и НЕ дублируется внутри художественного описания
    leaked = [cid for cid in CLASSES
              if any(w in (CLASSES[cid].get("desc") or "")
                     for w in ("от Силы", "от Ловкости", "от Интеллекта", "от Духа"))]
    check("из описаний убраны формулировки «урон от <характеристики>»",
          not leaked, leaked)


# ───────────────── 19. уровень в карточке предмета ─────────────────

def test_item_caption_shows_own_level():
    print("\n[19] Рядом с требованием — свой уровень")
    ch = _hero(level=8)
    gear = next((k for k, v in ITEMS.items()
                 if isinstance(v, dict) and "#" not in k and v.get("slot")
                 and v.get("type") in ("weapon", "armor")), None)
    check("нашёлся предмет со слотом для проверки", gear is not None)
    if gear:
        cap = ui.item_caption(gear, "shop", ch)
        check("в карточке указан требуемый уровень", "Требуется уровень" in cap, cap[:120])
        check("и рядом свой уровень", f"(у вас {ch.level})" in cap, cap[:160])


# ───────────────── 20. формулировка отдыха ─────────────────

def test_rested_wording():
    print("\n[20] Понятная формулировка запаса отдыха")
    ch = _hero()
    ch.flags["rested"] = 250
    card = ui.render_stats(ch)
    check("старая формулировка убрана", "Отдохнувший опыт" not in card)
    check("новая говорит, что именно даёт отдых",
          "Двойной опыт" in card, [l for l in card.splitlines() if "💤" in l])


def main():
    print("=" * 56)
    print("UI-ПРАВКИ ПО ОТЧЁТУ БЕТЫ")
    print("=" * 56)
    test_sell_price_visible()
    test_titles_back_target()
    test_class_card_primary()
    test_item_caption_shows_own_level()
    test_rested_wording()
    print("\n" + "=" * 56)
    print(f"ИТОГО: ✅ {_passed} пройдено, ❌ {_failed} провалено")
    print("=" * 56)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
