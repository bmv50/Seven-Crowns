# -*- coding: utf-8 -*-
"""Тесты процедурного генератора зон. Запуск: python test_worldgen.py"""
from collections import deque

from engine import worldgen as wg


def _spec(size=40):
    return wg.RegionSpec(
        region_id="gloomwood", name="Сумрачный Лес",
        biomes=["Чаща", "Топь", "Бурелом"], size=size,
        mob_pool=[("крыса", 3.0), ("летучая_мышь", 1.0)],
    )


def test_size_and_schema():
    rooms = wg.generate_zone(_spec(40), base_seed=42)
    assert len(rooms) == 40
    for rid, r in rooms.items():
        assert rid.startswith("wild_gloomwood_")
        assert r["name"] and r["zone"] == "Сумрачный Лес" and r["desc"]
        assert r.get("wild") is True and r["biome"] in ("Чаща", "Топь", "Бурелом")
        assert isinstance(r["exits"], dict) and isinstance(r["spawns"], list)
    print("✓ размер и схема комнат")


def test_connectivity():
    for sz in (1, 5, 25, 60, 120):
        rooms = wg.generate_zone(_spec(sz), base_seed=7)
        ent = wg.entrance_id(_spec(sz))
        assert wg.validate_connected(rooms, ent), f"зона size={sz} не связна"
    print("✓ связность (BFS) для разных размеров")


def test_bidirectional_exits():
    rooms = wg.generate_zone(_spec(50), base_seed=3)
    for rid, r in rooms.items():
        for d, dst in r["exits"].items():
            assert dst in rooms, f"{rid}: выход в несуществующую {dst}"
            back = wg.REVERSE[d]
            assert rooms[dst]["exits"].get(back) == rid, f"{rid}↔{dst} не двусторонний"
    print("✓ все выходы двусторонние и ведут внутрь зоны")


def test_determinism():
    a = wg.generate_zone(_spec(40), base_seed=42)
    b = wg.generate_zone(_spec(40), base_seed=42)
    c = wg.generate_zone(_spec(40), base_seed=99)
    assert a.keys() == b.keys()
    assert all(a[k]["exits"] == b[k]["exits"] for k in a)        # тот же seed → тот же мир
    assert a.keys() != c.keys() or any(a[k]["name"] != c[k]["name"] for k in a)  # другой seed → другой
    print("✓ детерминизм по seed")


def test_entrance_no_spawns():
    rooms = wg.generate_zone(_spec(40), base_seed=42)
    ent = wg.entrance_id(_spec(40))
    assert rooms[ent].get("entrance") is True
    assert rooms[ent]["spawns"] == []      # на входе мобов нет (безопасный порог)
    print("✓ вход помечен и без мобов")


def test_attach_safe():
    spec = _spec(30)
    rooms = wg.generate_zone(spec, base_seed=5)
    world = {
        "village": {"name": "Площадь", "exits": {"север": "market"}},
        "market": {"name": "Рынок", "exits": {"юг": "village"}},
    }
    # Направление подключения задаёт САМА зона: вход держит одну сторону
    # свободной (reserved_dir), и подвеситься можно только с неё.
    ent = wg.entrance_id(spec)
    into = wg.REVERSE[rooms[ent]["reserved_dir"]]
    ok = wg.attach(world, rooms, "village", into, spec)
    assert ok
    assert world["village"]["exits"][into] == ent
    assert world[ent]["exits"][wg.REVERSE[into]] == "village"   # обратный путь
    assert len(world) == 2 + len(rooms)
    # занятое направление не перезаписываем
    rooms2 = wg.generate_zone(_spec(10), base_seed=8)
    assert wg.attach(world, rooms2, "village", "север", _spec(10)) is False
    assert world["village"]["exits"]["север"] == "market"  # рукотворное цело
    print("✓ attach: подвешивает на свободное, не ломает существующее")


def test_world_still_valid_after_attach():
    # после привязки ссылочная целостность мира не нарушена
    spec = _spec(35)
    rooms = wg.generate_zone(spec, base_seed=11)
    from engine.content import WORLD
    snapshot = dict(WORLD)
    test_world = {k: dict(v) for k, v in snapshot.items()}
    # направление задаёт зона (см. test_attach_safe), якорь ищем под него
    into = wg.REVERSE[rooms[wg.entrance_id(spec)]["reserved_dir"]]
    anchor = next(rid for rid, r in test_world.items()
                  if into not in r.get("exits", {}) and not r.get("teleport"))
    assert wg.attach(test_world, rooms, anchor, into, spec)
    # все выходы ведут в существующие комнаты
    for rid, r in test_world.items():
        for d, dst in r.get("exits", {}).items():
            assert dst in test_world, f"{rid}:{d}→{dst} битый"
    print("✓ мир остаётся ссылочно целостным после attach")


def test_entrance_keeps_free_side():
    """Регресс: генератор мог занять все 4 стороны входной комнаты, и тогда
    attach() затирал её внутренний выход — кусок зоны молча осиротевал
    (в gloomwood и oldmine так терялось по 8 комнат). Теперь одна сторона
    входа резервируется под связь с внешним миром."""
    for spec in wg.WILD_ZONE_SPECS:
        rooms = wg.generate_zone(spec, base_seed=42)
        ent = rooms[wg.entrance_id(spec)]
        res = ent.get("reserved_dir")
        assert res in wg.DIRS, f"{spec.region_id}: не объявлена сторона входа"
        assert res not in ent["exits"], \
            f"{spec.region_id}: резерв '{res}' занят внутренним выходом"
    print("✓ вход зоны всегда держит одну сторону свободной")


def test_attach_never_orphans_rooms():
    """attach() либо подключает зону целиком, либо отказывает — но никогда не
    оставляет недостижимых комнат."""
    for spec in wg.WILD_ZONE_SPECS:
        rooms = wg.generate_zone(spec, base_seed=42)
        ent = wg.entrance_id(spec)
        free_at_entrance = set(wg.DIRS) - set(rooms[ent]["exits"])
        assert rooms[ent]["reserved_dir"] in free_at_entrance
        for d in wg.DIRS:
            world = {"hub": {"name": "Хаб", "exits": {}}}
            ok = wg.attach(world, {k: dict(v) for k, v in rooms.items()}, "hub", d, spec)
            # успех ровно тогда, когда обратная сторона входа свободна
            assert ok == (wg.REVERSE[d] in free_at_entrance), \
                f"{spec.region_id}: attach({d}) == {ok}"
            if not ok:
                continue
            seen, q = {"hub"}, deque(["hub"])
            while q:
                for dst in (world[q.popleft()].get("exits") or {}).values():
                    if dst in world and dst not in seen:
                        seen.add(dst); q.append(dst)
            assert len(seen) == len(world), \
                f"{spec.region_id}: осиротело {len(world) - len(seen)} комнат"
    print("✓ attach не оставляет осиротевших комнат ни при каком направлении")


def test_specs_reference_existing_mobs():
    """Все мобы в пулах готовых зон существуют. Без этого ошибка вылезет только
    при WILD_ZONES=1, то есть в проде, а не в тестах."""
    from engine.content import MOBS
    for spec in wg.WILD_ZONE_SPECS:
        assert spec.mob_pool, f"{spec.region_id}: пустой пул мобов"
        for mid, w in spec.mob_pool:
            assert mid in MOBS, f"{spec.region_id}: моба '{mid}' нет в mobs.yaml"
            assert w > 0, f"{spec.region_id}: неположительный вес у '{mid}'"
    print("✓ пулы готовых зон ссылаются на существующих мобов")


def test_specs_match_declared_level_range():
    """Уровень мобов пула не выходит за объявленный level_range: подпись
    «(ур.17–19)» в шапке комнаты обязана быть правдой, иначе это ловушка."""
    from engine.content import MOBS
    for spec in wg.WILD_ZONE_SPECS:
        assert spec.level_range, f"{spec.region_id}: не объявлен level_range"
        lo, hi = spec.level_range
        for mid, _w in spec.mob_pool:
            lv = MOBS[mid].get("level", 1)
            assert lo <= lv <= hi, \
                f"{spec.region_id} обещает ур.{lo}–{hi}, а '{mid}' — ур.{lv}"
        assert f"ур.{lo}" in spec.zone_label
    print("✓ level_range регионов честен относительно пула мобов")


def test_band_17_to_cap_is_covered():
    """Полоса 17–кап перестала быть горлышком: у каждого уровня в ней есть
    заметное число спавнов. Это и есть смысл задачи (было 8 комнат на всю полосу)."""
    from engine.content import MOBS
    from engine.character import LEVEL_CAP
    world = {}
    for spec in wg.WILD_ZONE_SPECS:
        world.update(wg.generate_zone(spec, base_seed=42))
    counts = {}
    for r in world.values():
        for m in r.get("spawns", []):
            lv = MOBS[m].get("level", 1)
            if 17 <= lv <= LEVEL_CAP:
                counts[lv] = counts.get(lv, 0) + 1
    band = list(range(17, LEVEL_CAP + 1))
    missing = [lv for lv in band if counts.get(lv, 0) < 3]
    assert not missing, f"уровни без покрытия дикими зонами: {missing} ({counts})"
    print(f"✓ полоса 17–{LEVEL_CAP} покрыта: {sum(counts.values())} спавнов, "
          f"минимум {min(counts[lv] for lv in band)} на уровень")


def test_anchor_matches_level():
    """Якорь выбирается по уровню: вход в зону 23–25 не должен открываться из
    стартовой деревни, а зона 1–3 — из Бездны."""
    import copy
    from engine.content import MOBS, WORLD as _W
    world = {k: copy.deepcopy(v) for k, v in _W.items() if not v.get("wild")}
    mob_levels = {m: d.get("level", 1) for m, d in MOBS.items()}
    attached = wg.apply_wild_zones(world, base_seed=42, mob_levels=mob_levels)
    assert attached, "ни одна зона не подвесилась"
    by_id = {s.region_id: s for s in wg.WILD_ZONE_SPECS}
    for region_id, anchor, _d in attached:
        want = by_id[region_id].level_range[0]
        got = wg._room_level(world[anchor], mob_levels)
        assert abs(got - want) <= 4, \
            f"{region_id} (ур.{want}) подвешен к '{anchor}' ур.{got}"
    print("✓ якоря зон соответствуют их уровню (±4)")


if __name__ == "__main__":
    test_size_and_schema()
    test_connectivity()
    test_bidirectional_exits()
    test_determinism()
    test_entrance_no_spawns()
    test_attach_safe()
    test_world_still_valid_after_attach()
    test_entrance_keeps_free_side()
    test_attach_never_orphans_rooms()
    test_specs_reference_existing_mobs()
    test_specs_match_declared_level_range()
    test_band_17_to_cap_is_covered()
    test_anchor_matches_level()
    print("\n=== worldgen OK ===")
