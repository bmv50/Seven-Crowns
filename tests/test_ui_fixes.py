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


# ─────────── Читаемость боя: слабости цели и комбо видны игроку ───────────
def test_combat_marks_show_consequences():
    """Раньше в бою подсвечивался ровно один частный случай (духи), поэтому
    выбор умения решением не был: движок считал резисты, игрок их не видел."""
    from engine import rules2, combat
    from engine.world import MobInstance

    class _W:
        def __init__(self, m): self.m = m
        def find(self, room, key): return self.m

    _was = rules2.ENABLED
    rules2.ENABLED = True
    try:
        ch = Character(uid=1, name="Т", cls="mage", race="human", level=20)
        ch.init_skills(); ch.init_vitals()

        # (1) кнопка обычной атаки помечается по типу урона ОРУЖИЯ
        golem = MobInstance("k", "кристальный_голем", "r")   # уязв. огонь, резист холод
        ch.target = golem.key
        ch.equipment["weapon"] = "посох_тумана"              # bash — обычный урон
        labels = [b.text for row in ui.kb_combat(ch, _W(golem)).inline_keyboard for b in row]
        fire = [l for l in labels if "Огненный шар" in l]
        check("умение в уязвимость помечено 🔻", fire and fire[0].startswith("🔻"), labels)

        # (2) сопротивление цели помечается отдельно от уязвимости
        spirit = MobInstance("k2", "лесной_дух", "r")        # резист режущее/колющее
        wch = Character(uid=2, name="В", cls="warrior", race="human", level=20)
        wch.init_skills(); wch.init_vitals()
        wch.equipment["weapon"] = "стальной_меч"             # slash — резистится
        wch.target = spirit.key
        atk = [b.text for row in ui.kb_combat(wch, _W(spirit)).inline_keyboard
               for b in row if b.callback_data and b.callback_data.startswith("atk:")]
        check("атака резистируемым оружием помечена 🛡", atk and atk[0].startswith("🛡"), atk)

        # (3) комбо по льду: заморожен → следующий удар гарантированный крит
        golem.effects.append({"type": "freeze", "turns": 2})
        atk2 = [b.text for row in ui.kb_combat(ch, _W(golem)).inline_keyboard
                for b in row if b.callback_data and b.callback_data.startswith("atk:")]
        check("комбо «раскол» видно на кнопке атаки",
              atk2 and "раскол" in atk2[0], atk2)

        # (4) лечение/бафф пометок не получают — «тип урона» у них бессмыслен
        pch = Character(uid=3, name="Ж", cls="priest", race="human", level=20)
        pch.init_skills(); pch.init_vitals(); pch.target = golem.key
        heal = [b.text for row in ui.kb_combat(pch, _W(golem)).inline_keyboard
                for b in row if "Исцеление" in b.text]
        check("лечение не помечается типом урона",
              heal and not any(heal[0].startswith(m) for m in ui.DTYPE_MARK.values()), heal)

        # (5) при выключенном ядре правил пометок нет вовсе
        rules2.ENABLED = False
        plain = [b.text for row in ui.kb_combat(ch, _W(golem)).inline_keyboard for b in row]
        check("без RULES_V2 пометок нет",
              not any(l.startswith(m) for l in plain for m in ui.DTYPE_MARK.values()), plain)
    finally:
        rules2.ENABLED = _was


def test_immunity_blocks_dot():
    """Иммунитет к типу урона отменяет и соответствующий DoT: иначе интерфейс
    показывал бы «🚫 не берёт: яд» и рядом «☠️ яд 3»."""
    from engine import rules2, combat
    from engine.world import MobInstance
    _was = rules2.ENABLED
    rules2.ENABLED = True
    try:
        undead = MobInstance("k", "скелет", "r")     # immune: poison, disease
        msg = combat.apply_status(undead, {"type": "poison", "turns": 3, "dmg": 4})
        check("яд не ложится на иммунного к яду",
              "невосприимчив" in msg and not undead.effects, msg)
        msg2 = combat.apply_status(undead, {"type": "burn", "turns": 3, "dmg": 4})
        check("горение на ту же нежить ложится (иммунитета к огню нет)",
              "горит" in msg2 and any(e["type"] == "burn" for e in undead.effects), msg2)
        beast = MobInstance("k2", "волк", "r")
        combat.apply_status(beast, {"type": "poison", "turns": 3, "dmg": 4})
        check("на обычного моба яд ложится как раньше",
              any(e["type"] == "poison" for e in beast.effects))
    finally:
        rules2.ENABLED = _was


def test_windup_gives_every_class_an_answer():
    """Замах вводит решение в бой — но только если ответ есть у КАЖДОГО класса.
    Контроль (срывает замах) есть лишь у мага и паладина, поэтому щит/уклонение
    и лечение тоже считаются ответом и подсвечиваются."""
    from engine import rules2, combat
    from engine.world import MobInstance
    from engine.content import CLASSES as _CLS

    class _W:
        def __init__(self, m): self.m = m
        def find(self, room, key): return self.m

    _r, _tg = rules2.ENABLED, combat.ENABLED_TELEGRAPH
    rules2.ENABLED = True
    combat.ENABLED_TELEGRAPH = True
    try:
        for cls in _CLS:
            ch = Character(uid=1, name="Т", cls=cls, race="human", level=22)
            ch.init_skills(); ch.init_vitals(); ch.mp = ch.max_resource
            mob = MobInstance("k", "глубинный_кошмар", "r")
            ch.target = mob.key
            combat.start_windup(mob)
            labels = [b.text for row in ui.kb_combat(ch, _W(mob)).inline_keyboard for b in row]
            check(f"{cls}: во время замаха подсвечен ответ",
                  any(l.startswith(m) for l in labels for m in ui.WINDUP_MARK.values()),
                  labels)
            check(f"{cls}: значок не задваивается",
                  not any(l[:2] == l[2:4] for l in labels if len(l) > 4), labels)
    finally:
        rules2.ENABLED, combat.ENABLED_TELEGRAPH = _r, _tg


def test_windup_mechanics():
    """Механика замаха: предупреждение → тяжёлый удар, контроль его срывает."""
    import random
    from engine import combat
    from engine.world import MobInstance
    _tg = combat.ENABLED_TELEGRAPH
    combat.ENABLED_TELEGRAPH = True
    try:
        mob = MobInstance("k", "глубинный_кошмар", "r")
        check("свежий моб не в замахе", not combat.is_winding_up(mob))
        combat.start_windup(mob)
        check("после start_windup моб в замахе", combat.is_winding_up(mob))
        check("замах не считается контролем (моб не парализован)",
              not combat.mob_is_disabled(mob))
        check("clear_windup снимает замах и сообщает об этом",
              combat.clear_windup(mob) is True and not combat.is_winding_up(mob))
        check("повторный clear_windup возвращает False",
              combat.clear_windup(mob) is False)

        # тяжёлый удар действительно тяжелее обычного
        random.seed(4)
        def avg(heavy):
            tot = 0
            for _ in range(40):
                c = Character(uid=2, name="Ц", cls="warrior", race="human", level=20)
                c.init_vitals()
                combat.mob_attack(mob, c, heavy=heavy)
                tot += c.max_hp - c.hp
            return tot / 40
        light, heavy = avg(False), avg(True)
        check(f"удар из замаха тяжелее обычного ({light:.0f} → {heavy:.0f})",
              heavy > light * 1.5, f"{light:.0f} vs {heavy:.0f}")

        # при выключенном флаге замахов не бывает вовсе
        combat.ENABLED_TELEGRAPH = False
        fresh = MobInstance("k2", "глубинный_кошмар", "r")
        check("с TELEGRAPH=0 замах не начинается",
              not any(combat.telegraph_due(fresh) for _ in range(200)))
    finally:
        combat.ENABLED_TELEGRAPH = _tg


def main():
    print("=" * 56)
    print("UI-ПРАВКИ ПО ОТЧЁТУ БЕТЫ")
    print("=" * 56)
    test_sell_price_visible()
    test_titles_back_target()
    test_class_card_primary()
    test_item_caption_shows_own_level()
    test_rested_wording()
    test_combat_marks_show_consequences()
    test_immunity_blocks_dot()
    test_windup_gives_every_class_an_answer()
    test_windup_mechanics()
    print("\n" + "=" * 56)
    print(f"ИТОГО: ✅ {_passed} пройдено, ❌ {_failed} провалено")
    print("=" * 56)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
