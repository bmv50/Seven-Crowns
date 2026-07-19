# -*- coding: utf-8 -*-
"""
Процедурный генератор «диких» зон (адаптация из референса TeleMud, Go-генератор).

Делает СВЯЗНЫЙ граф комнат в формате WORLD (engine/content): 2D random-walk по
сетке + остовное дерево (гарантирует связность) + добавочные рёбра-петли + BFS-
проверка. Детерминирован по seed (один seed → один и тот же мир).

НЕ трогает рукотворное ядро. Зоны генерируются опционально и «подвешиваются» к
существующей комнате-якорю через attach(). Назначение — дёшево набивать дикие
окраины/случайные подземелья вокруг handcrafted-локаций и квестов.

Комнаты на выходе совместимы со схемой WORLD:
    {name, zone, desc, exits{dir:dest}, spawns[mob_id], wild=True, biome}
Ключи комнат: "wild_<region>_<x>_<y>" — не пересекаются с рукотворными.

Модуль НЕ импортирует engine.content — мир принимается аргументом (world), что
исключает циклическую зависимость.
"""
import random
from collections import deque
from typing import Dict, List, Optional, Tuple

# направления и их обратные (как в world.yaml)
DIRS = {
    "север": (0, -1, "юг"),
    "юг":    (0, 1, "север"),
    "восток": (1, 0, "запад"),
    "запад":  (-1, 0, "восток"),
}
REVERSE = {d: rev for d, (_, _, rev) in DIRS.items()}


class RegionSpec:
    """Описание генерируемого региона."""
    def __init__(self, region_id: str, name: str, biomes: List[str],
                 size: int = 40, mob_pool: Optional[List[Tuple[str, float]]] = None,
                 spawn_chance: float = 0.6, max_spawns: int = 2,
                 loop_chance: float = 0.18):
        self.region_id = region_id
        self.name = name
        self.biomes = biomes
        self.size = max(1, size)
        self.mob_pool = mob_pool or []      # [(mob_id, weight), ...]
        self.spawn_chance = spawn_chance
        self.max_spawns = max_spawns
        self.loop_chance = loop_chance


def _derive_seed(base_seed: int, region_id: str) -> int:
    """Детерминированный seed региона = base XOR хэш(region_id)."""
    h = 1469598103934665603
    for ch in region_id:
        h = (h ^ ord(ch)) * 1099511628211 & 0xFFFFFFFFFFFFFFFF
    return (base_seed ^ h) & 0x7FFFFFFFFFFFFFFF


def _rid(region_id: str, x: int, y: int) -> str:
    return f"wild_{region_id}_{x}_{y}"


def _room_text(rng: random.Random, biome: str, region_name: str) -> Tuple[str, str]:
    qualifiers = ["Глухой", "Заброшенный", "Туманный", "Тихий", "Гиблый",
                  "Дальний", "Сумрачный", "Древний"]
    flavor = [
        "Туман липнет к земле, скрадывая звуки.",
        "Под ногами чавкает сырая почва, где-то капает вода.",
        "Воздух тяжёлый, пахнет прелой листвой и тленом.",
        "Ветер несёт далёкий вой, от которого стынет кровь.",
        "Кости мелких зверей белеют в траве.",
        "Старые следы ведут куда-то вглубь и обрываются.",
    ]
    name = f"{rng.choice(qualifiers)} {biome.lower()}"
    desc = f"{biome} на окраине региона «{region_name}». {rng.choice(flavor)}"
    return name, desc


def _pick_spawns(rng: random.Random, spec: RegionSpec) -> List[str]:
    if not spec.mob_pool or rng.random() > spec.spawn_chance:
        return []
    ids = [m for m, _ in spec.mob_pool]
    weights = [w for _, w in spec.mob_pool]
    n = rng.randint(1, spec.max_spawns)
    return rng.choices(ids, weights=weights, k=n)


def generate_zone(spec: RegionSpec, base_seed: int = 42) -> Dict[str, dict]:
    """Сгенерировать связную зону. Возвращает {room_id: room_dict}."""
    rng = random.Random(_derive_seed(base_seed, spec.region_id))

    # 1) рост клеток random-walk'ом от центра + остовное дерево (parent edges)
    start = (0, 0)
    cells: Dict[Tuple[int, int], dict] = {}
    parent_edge: Dict[Tuple[int, int], Tuple[Tuple[int, int], str]] = {}
    queue = [start]
    cells[start] = {}
    while queue and len(cells) < spec.size:
        idx = rng.randrange(len(queue))
        cx, cy = queue.pop(idx)
        dirs = list(DIRS.items())
        rng.shuffle(dirs)
        for d, (dx, dy, _rev) in dirs:
            if len(cells) >= spec.size:
                break
            npt = (cx + dx, cy + dy)
            if npt in cells:
                continue
            if rng.random() < 0.85:
                cells[npt] = {}
                parent_edge[npt] = ((cx, cy), d)
                queue.append(npt)

    # 2) рёбра: остовное дерево (связность) + добавочные петли
    exits: Dict[Tuple[int, int], Dict[str, Tuple[int, int]]] = {pt: {} for pt in cells}

    def link(a, b, d):
        exits[a][d] = b
        exits[b][REVERSE[d]] = a

    for child, (par, d) in parent_edge.items():
        link(par, child, d)
    # добавочные рёбра между соседними клетками (петли/альтернативные пути)
    for (x, y) in cells:
        for d, (dx, dy, _rev) in DIRS.items():
            nb = (x + dx, y + dy)
            if nb in cells and d not in exits[(x, y)] and rng.random() < spec.loop_chance:
                link((x, y), nb, d)

    # 3) сборка комнат в формате WORLD
    rooms: Dict[str, dict] = {}
    for (x, y) in cells:
        biome = rng.choice(spec.biomes)
        name, desc = _room_text(rng, biome, spec.name)
        rid = _rid(spec.region_id, x, y)
        rooms[rid] = {
            "name": name,
            "zone": spec.name,
            "desc": desc,
            "biome": biome,
            "wild": True,
            "exits": {d: _rid(spec.region_id, nx, ny)
                      for d, (nx, ny) in exits[(x, y)].items()},
            "spawns": [] if (x, y) == start else _pick_spawns(rng, spec),
        }
    rooms[_rid(spec.region_id, *start)]["entrance"] = True
    return rooms


def entrance_id(spec: RegionSpec) -> str:
    return _rid(spec.region_id, 0, 0)


def validate_connected(rooms: Dict[str, dict], start: str) -> bool:
    """BFS: достижимы ли все комнаты зоны из входа (через рёбра внутри зоны)."""
    if start not in rooms:
        return False
    seen = {start}
    q = deque([start])
    while q:
        cur = q.popleft()
        for dst in rooms[cur].get("exits", {}).values():
            if dst in rooms and dst not in seen:
                seen.add(dst)
                q.append(dst)
    return len(seen) == len(rooms)


def attach(world: Dict[str, dict], rooms: Dict[str, dict],
           anchor_room: str, direction: str, spec: RegionSpec) -> bool:
    """
    Подвесить зону к рукотворному миру: соединить anchor_room ←→ вход зоны.
    direction — направление ИЗ anchor_room в дикую зону. Возвращает True при успехе.
    Не перезаписывает существующие комнаты/выходы.
    """
    if anchor_room not in world or direction not in DIRS:
        return False
    if direction in world[anchor_room].get("exits", {}):
        return False  # это направление уже занято — не ломаем рукотворное
    ent = entrance_id(spec)
    if ent not in rooms:
        return False
    # перенести комнаты (без затирания)
    for rid, r in rooms.items():
        if rid not in world:
            world[rid] = r
    world[anchor_room].setdefault("exits", {})[direction] = ent
    rooms[ent].setdefault("exits", {})[REVERSE[direction]] = anchor_room
    return True


# ───────── готовые дикие зоны (опционально, env WILD_ZONES=1) ─────────
WILD_ZONE_SPECS = [
    RegionSpec("gloomwood", "Сумрачный Лес", ["Чаща", "Бурелом", "Папоротниковая лощина"],
               size=30, spawn_chance=0.55,
               mob_pool=[("волк", 3.0), ("порченый_лис", 2.0), ("лунопряха", 1.0)]),
    RegionSpec("oldmine", "Заброшенные Штольни", ["Штрек", "Обвал", "Рудный зал"],
               size=24, spawn_chance=0.6,
               mob_pool=[("рудничная_крыса", 3.0), ("штрековый_кобольдёныш", 2.0),
                         ("пещерный_слизень", 1.0)]),
]


def _pick_anchor(world, used_dirs):
    """Детерминированно выбрать комнату-якорь дикой природы со свободным направлением."""
    for rid in sorted(world):
        r = world[rid]
        if r.get("teleport") or r.get("safe") or r.get("wild"):
            continue
        if not r.get("spawns"):
            continue
        taken = set(r.get("exits", {})) | used_dirs.get(rid, set())
        for d in DIRS:
            if d not in taken:
                return rid, d
    return None


def apply_wild_zones(world, base_seed=42, specs=None):
    """Сгенерировать и подвесить набор диких зон. Возвращает [(region_id, anchor, dir)]."""
    specs = specs if specs is not None else WILD_ZONE_SPECS
    attached = []
    used_dirs = {}
    for spec in specs:
        if entrance_id(spec) in world:
            continue
        pick = _pick_anchor(world, used_dirs)
        if not pick:
            break
        anchor, direction = pick
        rooms = generate_zone(spec, base_seed=base_seed)
        if attach(world, rooms, anchor, direction, spec):
            used_dirs.setdefault(anchor, set()).add(direction)
            attached.append((spec.region_id, anchor, direction))
    return attached
