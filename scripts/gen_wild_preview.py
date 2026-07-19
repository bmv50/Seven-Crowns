# -*- coding: utf-8 -*-
"""
Предпросмотр процедурной дикой зоны в консоли (ASCII-карта).
Ничего не меняет в игре — просто показывает, что сгенерирует worldgen.

Примеры:
  python scripts/gen_wild_preview.py                       # дефолтная зона
  python scripts/gen_wild_preview.py --region gloomwood    # одна из готовых зон
  python scripts/gen_wild_preview.py --size 24 --seed 7
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import worldgen as wg  # noqa: E402


def ascii_map(spec, base_seed):
    rooms = wg.generate_zone(spec, base_seed=base_seed)
    # координаты из ключа wild_<rid>_<x>_<y>
    pts = {}
    for rid in rooms:
        parts = rid.rsplit("_", 2)
        x, y = int(parts[1]), int(parts[2])
        pts[(x, y)] = rid
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    print(f"\nЗона «{spec.name}» [{spec.region_id}] — {len(rooms)} комнат, "
          f"seed={base_seed}, связна={wg.validate_connected(rooms, wg.entrance_id(spec))}")
    for y in range(miny, maxy + 1):
        # строка комнат
        row = ""
        for x in range(minx, maxx + 1):
            if (x, y) in pts:
                r = rooms[pts[(x, y)]]
                cell = "@" if r.get("entrance") else ("M" if r.get("spawns") else "o")
                east = "─" if "восток" in r["exits"] else " "
                row += cell + east
            else:
                row += "  "
        print(row)
        # строка вертикальных связей
        if y < maxy:
            link = ""
            for x in range(minx, maxx + 1):
                if (x, y) in pts and "юг" in rooms[pts[(x, y)]]["exits"]:
                    link += "│ "
                else:
                    link += "  "
            print(link)
    print("\nЛегенда: @ вход · M комната с мобами · o пустая · ─│ проходы")
    spawns = sorted({m for r in rooms.values() for m in r["spawns"]})
    print("Мобы в зоне:", ", ".join(spawns) or "—")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", choices=[s.region_id for s in wg.WILD_ZONE_SPECS])
    ap.add_argument("--size", type=int, default=0)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    if args.region:
        spec = next(s for s in wg.WILD_ZONE_SPECS if s.region_id == args.region)
    else:
        spec = wg.WILD_ZONE_SPECS[0]
    if args.size:
        spec.size = args.size
    ascii_map(spec, args.seed)


if __name__ == "__main__":
    main()
