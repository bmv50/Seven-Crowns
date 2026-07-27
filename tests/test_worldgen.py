# -*- coding: utf-8 -*-
"""Тесты процедурного генератора зон. Запуск: python test_worldgen.py"""
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
    # подвесить на свободное направление
    ok = wg.attach(world, rooms, "village", "восток", spec)
    assert ok
    ent = wg.entrance_id(spec)
    assert world["village"]["exits"]["восток"] == ent
    assert world[ent]["exits"]["запад"] == "village"     # обратный путь
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
    # найдём комнату со свободным «восток»
    anchor = next(rid for rid, r in test_world.items()
                  if "восток" not in r.get("exits", {}) and not r.get("teleport"))
    assert wg.attach(test_world, rooms, anchor, "восток", spec)
    # все выходы ведут в существующие комнаты
    for rid, r in test_world.items():
        for d, dst in r.get("exits", {}).items():
            assert dst in test_world, f"{rid}:{d}→{dst} битый"
    print("✓ мир остаётся ссылочно целостным после attach")


if __name__ == "__main__":
    test_size_and_schema()
    test_connectivity()
    test_bidirectional_exits()
    test_determinism()
    test_entrance_no_spawns()
    test_attach_safe()
    test_world_still_valid_after_attach()
    print("\n=== worldgen OK ===")
