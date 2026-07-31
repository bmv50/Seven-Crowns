# -*- coding: utf-8 -*-
"""
Источники добычи (отчёт тестировщика, пункты 11 и 12).
Запуск:
    python run_tests.py loot_sources

Баг: с городского голубя первого уровня падали серебряное кольцо и компас
странника — предметы, чьи описания обещают совсем другой источник. Причина
была не в таблице голубя (там только перо), а в ОБЩЕМ генераторе снаряжения:
он подбирал вещи по уровню моба, не глядя, кто это.

Два правила, которые теперь это чинят:
  • зверьё (rules2-категория beast, кроме боссов) не роняет кованых вещей —
    только собственный лут: шкуры, яд, перья;
  • предметы с unique_source исключены из общей таблицы и падают лишь со
    своего моба, поэтому описание не может разойтись с механикой.
"""
import sys

from engine.content import ITEMS, MOBS
from engine import rules2
from engine import loop as _loop

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


def _unique_items():
    return [k for k, v in ITEMS.items()
            if isinstance(v, dict) and v.get("unique_source")]


def _sources_of(item: str):
    return [k for k, v in MOBS.items()
            if any(i == item for i, _ in (v.get("loot") or []))]


def test_unique_out_of_generic_pool():
    print("\n[1] Предметы с именным источником вне общей таблицы")
    uniq = _unique_items()
    check("такие предметы вообще размечены", len(uniq) >= 5, uniq)
    leaked = [k for k in uniq if k in _loop._EQUIP_POOL]
    check("ни один не попал в общую таблицу дропа", not leaked, leaked)


def test_unique_still_obtainable():
    """Главный риск правки: пометили — и предмет стал недостижим."""
    print("\n[2] И при этом каждый остаётся добываемым")
    for item in _unique_items():
        src = _sources_of(item)
        check(f"{item} падает с {src or '—'}", bool(src),
              "нет ни одного моба с этим предметом в loot")


def test_desc_matches_reality():
    print("\n[3] Описание не расходится с источником")
    # компас обещает боцмана — проверяем именно это соответствие
    src = _sources_of("компас_странника")
    check("компас странника падает с утопшего боцмана",
          "утопший_боцман" in src, src)
    src = _sources_of("серебряное_кольцо")
    check("серебряное кольцо падает с паука (логово из описания)",
          any("паук" in s for s in src), src)


def test_beasts_drop_no_gear():
    print("\n[4] Зверьё не роняет кованое снаряжение")
    beasts = [k for k, v in MOBS.items()
              if rules2.mob_profile(v)["category"] == "beast" and not v.get("boss")]
    check("категория beast непуста (правило вообще применимо)",
          len(beasts) >= 10, len(beasts))
    check("городской голубь опознан как зверь",
          rules2.mob_profile(MOBS["городской_голубь"])["category"] == "beast",
          rules2.mob_profile(MOBS["городской_голубь"])["category"])

    # у зверей собственный лут — материалы, а не оружие и броня
    gear_in_beast_loot = []
    for k in beasts:
        for item, _ch in (MOBS[k].get("loot") or []):
            meta = ITEMS.get(item, {})
            if meta.get("type") in ("weapon", "armor") and meta.get("slot"):
                gear_in_beast_loot.append(f"{k}:{item}")
    check("в собственных таблицах зверей нет оружия и брони",
          not gear_in_beast_loot, gear_in_beast_loot)


def test_pool_still_healthy():
    """Правка не должна обескровить дроп: таблица обязана остаться большой."""
    print("\n[5] Общая таблица снаряжения не опустела")
    check("в таблице осталось много предметов",
          len(_loop._EQUIP_POOL) > 150, len(_loop._EQUIP_POOL))
    low = _loop._pool_for(3, 0)
    check("для мобов 3 уровня есть что ронять", len(low) >= 5, len(low))


def main():
    print("=" * 56)
    print("ИСТОЧНИКИ ДОБЫЧИ: зверьё и именные трофеи")
    print("=" * 56)
    test_unique_out_of_generic_pool()
    test_unique_still_obtainable()
    test_desc_matches_reality()
    test_beasts_drop_no_gear()
    test_pool_still_healthy()
    print("\n" + "=" * 56)
    print(f"ИТОГО: ✅ {_passed} пройдено, ❌ {_failed} провалено")
    print("=" * 56)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
