# -*- coding: utf-8 -*-
"""
Регенерация мобов вне боя (отчёт тестировщика, пункт 1).
Запуск:
    python run_tests.py mob_regen

Баг: HP возвращались мобу ТОЛЬКО при респавне после смерти, поэтому урон
копился между заходами и любого босса можно было добить тактикой
«ударил — отступил — вылечился — вернулся» без всякого риска.

Проверяем: в бою моб не лечится; после задержки начинает; лечится за
фактически прошедшее время, а не рывком; при полном восстановлении забывает
бой (эффекты, угроза, вклад) — иначе долю добычи получил бы и тот, кто ударил
моба час назад и ушёл.
"""
import sys

from engine.world import World, MobInstance

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


def _wounded_world(hp_frac=0.4, t0=1000.0):
    """Мир с одним раненым мобом вне боя. -> (world, mob, room)."""
    w = World()
    room, lst = next((r, l) for r, l in w.mobs.items() if l)
    mob = lst[0]
    mob.hp = int(mob.max_hp * hp_frac) or 1
    mob.aggro = []
    mob.last_hit_at = t0
    mob.last_regen_at = 0.0
    return w, mob, room


def test_no_regen_in_combat():
    print("\n[1] В бою моб не лечится")
    w, mob, _ = _wounded_world()
    mob.aggro = [777]                      # бой идёт
    hp0 = mob.hp
    w.process_regen(now=1000 + 600)        # десять минут «тишины» по таймеру
    check("с непустым аггро HP не меняется", mob.hp == hp0, f"{hp0} → {mob.hp}")


def test_delay_respected():
    print("\n[2] Задержка перед регенерацией соблюдается")
    w, mob, _ = _wounded_world()
    hp0 = mob.hp
    w.process_regen(now=1000 + World.REGEN_DELAY - 1)
    check("до истечения задержки лечения нет", mob.hp == hp0, f"{hp0} → {mob.hp}")
    w.process_regen(now=1000 + World.REGEN_DELAY + 5)
    check("после задержки лечение началось", mob.hp > hp0, f"{hp0} → {mob.hp}")


def test_gradual_not_instant():
    print("\n[3] Лечение постепенное, а не рывком")
    w, mob, _ = _wounded_world(hp_frac=0.1)
    # первый тик сразу после задержки: прошло 0 секунд регена — почти ничего
    w.process_regen(now=1000 + World.REGEN_DELAY)
    check("первый тик не восстанавливает всё",
          mob.hp < mob.max_hp, f"{mob.hp}/{mob.max_hp}")

    # секунда регена ≈ REGEN_PER_SEC от максимума
    hp_before = mob.hp
    w.process_regen(now=1000 + World.REGEN_DELAY + 1)
    gained = mob.hp - hp_before
    expected = mob.max_hp * World.REGEN_PER_SEC
    check("за секунду прибавка близка к расчётной",
          abs(gained - expected) <= max(1.0, expected * 0.25),
          f"прибавка {gained:.1f}, ожидали ~{expected:.1f}")


def test_full_heal_forgets_fight():
    print("\n[4] Полное восстановление обнуляет память о бое")
    w, mob, _ = _wounded_world(hp_frac=0.5)
    mob.effects = [{"type": "poison", "turns": 5}]
    mob.threat[42] = 100.0
    mob.contrib[42] = 250.0
    mob.exploited_by.add(42)

    # достаточно времени, чтобы долечиться до максимума
    w.process_regen(now=1000 + World.REGEN_DELAY + 1)
    w.process_regen(now=1000 + World.REGEN_DELAY + 120)

    check("HP восстановлены полностью", mob.hp == mob.max_hp, f"{mob.hp}/{mob.max_hp}")
    check("яды и заморозки сняты", mob.effects == [], mob.effects)
    check("угроза обнулена", not mob.threat, mob.threat)
    check("вклад в бой обнулён (иначе доля достанется ушедшему)",
          not mob.contrib, mob.contrib)
    check("отметка слабости снята", not mob.exploited_by, mob.exploited_by)
    check("после полного HP регенерация больше не работает",
          not mob.took_part(42))


def test_hit_resets_timer():
    print("\n[5] Удар по мобу откладывает регенерацию")
    w, mob, _ = _wounded_world()
    hp0 = mob.hp
    mob.add_contrib(42, 10)                # попали — таймер тишины сброшен
    check("удар обновил метку последнего попадания", mob.last_hit_at > 1000.0)
    # с точки зрения таймера тишина только началась, лечиться рано
    w.process_regen(now=mob.last_hit_at + World.REGEN_DELAY - 1)
    check("сразу после удара лечения нет", mob.hp == hp0, f"{hp0} → {mob.hp}")


def test_dead_mob_untouched():
    print("\n[6] Мёртвый моб регенерацией не трогается")
    w, mob, _ = _wounded_world()
    w.kill(mob)
    hp0 = mob.hp
    w.process_regen(now=1000 + 999)
    check("HP трупа не растут (этим занят респавн)", mob.hp == hp0, f"{hp0} → {mob.hp}")


def test_gather_nodes_not_on_ground():
    """Отчёт тестировщика, п.8: ресурс узла профессии не должен ЕЩЁ И лежать
    на земле в той же комнате — иначе кнопка «✋ Подобрать» обходит требование
    профессии, и весь навык обесценивается."""
    print("\n[7] Ресурсы профессий не дублируются предметами на земле")
    import yaml
    from engine.content import WORLD
    nodes = yaml.safe_load(open("data/professions.yaml", encoding="utf-8")).get("nodes", {})
    clashes = []
    for room, lst in (nodes or {}).items():
        ground = set(WORLD.get(room, {}).get("items") or [])
        for n in lst:
            if n["item"] in ground:
                clashes.append(f"{room}:{n['item']}")
    check("ни один узел добычи не продублирован на земле",
          not clashes, ", ".join(clashes))


def main():
    print("=" * 56)
    print("РЕГЕНЕРАЦИЯ МОБОВ ВНЕ БОЯ И ГЕЙТ ПРОФЕССИЙ")
    print("=" * 56)
    test_no_regen_in_combat()
    test_delay_respected()
    test_gradual_not_instant()
    test_full_heal_forgets_fight()
    test_hit_resets_timer()
    test_dead_mob_untouched()
    test_gather_nodes_not_on_ground()
    print("\n" + "=" * 56)
    print(f"ИТОГО: ✅ {_passed} пройдено, ❌ {_failed} провалено")
    print("=" * 56)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
