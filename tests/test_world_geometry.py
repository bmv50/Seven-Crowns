# -*- coding: utf-8 -*-
"""
Геометрия мира и честность карты (отчёт тестировщика, пункты 9 и 10).
Запуск:
    python run_tests.py world_geometry

Пункт 10: выход из катакомб «наверх» значился как «на север», а обратно Врата
вели «вниз» — направления в паре не сходились.
Пункт 9: на карте комната, лежащая к востоку, могла нарисоваться севернее.
Причина была в раскладке: при занятой клетке искалась ЛЮБАЯ свободная по
спирали, включая противоположную сторону.

Здесь три проверки: пары направлений согласованы; раскладка карты не
переворачивает направление; линии между комнатами рисуются только там, где
они не врут. Кольцо в Пепельных Пустошах на плоскую сетку не ложится в
принципе (пятиугольник со смещением ≠ 0), это известное исключение.
"""
import sys

from engine.content import WORLD

_passed = 0
_failed = 0

DIRS = {"север": (0, -1), "юг": (0, 1), "восток": (1, 0), "запад": (-1, 0)}
OPP = {"север": "юг", "юг": "север", "запад": "восток", "восток": "запад",
       "вверх": "вниз", "вниз": "вверх"}

# Кольцо Пепельных Пустошей: edge→field→bone_road→court и edge→chapel→court.
# Суммарное смещение по двум путям разное, поэтому на сетке кольцо не
# замыкается ни при какой расстановке. Карта такие рёбра просто не рисует.
NON_PLANAR = {"Пепельные Пустоши"}


def check(name, cond, extra=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✅ {name}")
    else:
        _failed += 1
        print(f"  ❌ {name}" + (f" — {extra}" if extra else ""))


def test_exit_pairs_consistent():
    print("\n[1] Направления переходов согласованы в обе стороны")
    bad = []
    for rid, r in WORLD.items():
        if r.get("wild"):
            continue
        for d, dst in (r.get("exits") or {}).items():
            dr = WORLD.get(dst)
            if not dr or dr.get("wild"):
                continue
            back = {dd for dd, t in (dr.get("exits") or {}).items() if t == rid}
            if back and OPP.get(d) not in back:
                bad.append(f"{rid} --{d}--> {dst}, обратно {sorted(back)}")
    check("нет пар вида «туда на север, обратно на запад»", not bad,
          "; ".join(bad[:4]))


def test_catacombs_exit_is_up():
    """Ровно тот случай, на который жаловался тестировщик."""
    print("\n[2] Из катакомб к Вратам ведёт «вверх», а не «на север»")
    ex = WORLD["catacombs"].get("exits") or {}
    check("выход к Вратам помечен «вверх»", ex.get("вверх") == "ruin_gate", ex)
    back = WORLD["ruin_gate"].get("exits") or {}
    check("Врата ведут в катакомбы «вниз»", back.get("вниз") == "catacombs", back)


def test_zone_geometry_embeddable():
    print("\n[3] Зоны укладываются на сетку без противоречий")
    import collections
    zones = collections.defaultdict(list)
    for rid, r in WORLD.items():
        if not r.get("wild"):
            zones[r.get("zone")].append(rid)
    for zone, rooms in sorted(zones.items(), key=lambda x: str(x[0])):
        pos = {rooms[0]: (0, 0)}
        queue = [rooms[0]]
        bad = []
        while queue:
            r = queue.pop(0)
            for d, dst in (WORLD[r].get("exits") or {}).items():
                if d not in DIRS or dst not in rooms:
                    continue
                dx, dy = DIRS[d]
                want = (pos[r][0] + dx, pos[r][1] + dy)
                if dst in pos:
                    if pos[dst] != want:
                        bad.append(f"{r}--{d}-->{dst}")
                else:
                    pos[dst] = want
                    queue.append(dst)
        if zone in NON_PLANAR:
            check(f"«{zone}»: известное непланарное кольцо (карта рёбра не рисует)",
                  bool(bad), "если противоречий не стало — уберите зону из NON_PLANAR")
        else:
            check(f"«{zone}»: противоречий нет", not bad, "; ".join(bad[:3]))


def test_map_layout_keeps_direction():
    print("\n[4] Раскладка карты не переворачивает направление")
    from bot import mapgen
    lies = set()
    total = 0
    for start in WORLD:
        coords = mapgen._layout_local(start)
        for rid in coords:
            for d, dst in (WORLD.get(rid, {}).get("exits") or {}).items():
                if d not in DIRS or dst not in coords:
                    continue
                dx, dy = DIRS[d]
                x0, y0 = coords[rid]
                x1, y1 = coords[dst]
                prim = (x1 - x0) * dx + (y1 - y0) * dy
                perp = abs((y1 - y0) if dx else (x1 - x0))
                total += 1
                if prim <= 0 or perp >= prim:
                    lies.add(f"{rid}--{d}-->{dst}")
    check(f"проверено {total} переходов на картах всех комнат", total > 500, total)
    # остаться могут только рёбра непланарного кольца
    outside = [x for x in lies
               if WORLD[x.split("--")[0]].get("zone") not in NON_PLANAR]
    check("вне известного кольца направление нигде не врёт", not outside,
          "; ".join(sorted(outside)[:4]))


def test_map_art_lookup():
    """Регрессия: после перевода артов в JPEG карта искала только .png, и
    изображения комнат в ячейках пропали — боксы стали пустыми."""
    print("\n[5] Карта находит арты комнат в любом формате")
    import os
    from bot import mapgen
    check("поиск перебирает расширения, а не только .png",
          ".jpg" in mapgen._IMG_EXT and ".webp" in mapgen._IMG_EXT,
          mapgen._IMG_EXT)
    have = [rid for rid in WORLD
            if mapgen._find_img(mapgen.ROOMS_IMG, rid)]
    if os.path.isdir(mapgen.ROOMS_IMG) and os.listdir(mapgen.ROOMS_IMG):
        check(f"арты комнат находятся ({len(have)} из {len(WORLD)})", bool(have),
              "ни одного — проверьте images/rooms/")
    else:
        print("  ~ папка артов пуста — проверка пропущена")


def test_map_zone_backgrounds_described():
    """Фон карты берётся по зоне; описание должно быть у каждой зоны, иначе
    часть карт молча получит общий безликий ландшафт."""
    print("\n[6] У каждой зоны есть описание фона карты")
    import importlib.util
    import os
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "scripts", "gen_map_backgrounds.py")
    if not os.path.exists(p):
        print("  ~ scripts/gen_map_backgrounds.py нет — проверка пропущена")
        return
    spec = importlib.util.spec_from_file_location("_gmb", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    zones = mod.zones_in_world()
    missing = [z for z in zones if z not in mod.ZONE_ART]
    check(f"описаны все {len(zones)} зон", not missing, missing)
    # запрет надписей обязан быть в промпте: модель рисует псевдобуквы
    for z in zones[:3]:
        check(f"«{z}»: в промпте есть запрет надписей",
              "Без единой буквы" in mod.build_prompt(z))


def main():
    print("=" * 56)
    print("ГЕОМЕТРИЯ МИРА И ЧЕСТНОСТЬ КАРТЫ")
    print("=" * 56)
    test_exit_pairs_consistent()
    test_catacombs_exit_is_up()
    test_zone_geometry_embeddable()
    test_map_layout_keeps_direction()
    test_map_art_lookup()
    test_map_zone_backgrounds_described()
    print("\n" + "=" * 56)
    print(f"ИТОГО: ✅ {_passed} пройдено, ❌ {_failed} провалено")
    print("=" * 56)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
