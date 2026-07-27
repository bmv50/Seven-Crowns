# -*- coding: utf-8 -*-
"""Популяционный прогон: N игроков (разные расы/классы) до целевого уровня."""
import asyncio, json, statistics, sys
from engine.content import RACES, CLASSES
from engine.character import Character
from engine.world import World
from engine.loop import GameLoop
import sim_player as sp

N_TOTAL = 100


def _roster(n):
    combos = []
    for race, rc in RACES.items():
        for cls in rc.get("allowed_classes", list(CLASSES)):
            combos.append((race, cls))
    return [combos[i % len(combos)] for i in range(n)]


async def _run_one(race, cls, target, budget):
    async def send(uid, text): pass
    async def save(c): pass
    world = World()
    ch = Character(uid=1, name="P", cls=cls, race=race)
    ch.init_vitals()
    gl = GameLoop(world, {1: ch}, send, save)
    gl.on_combat_reward = None
    res = await sp.simulate_to(ch, world, gl, target, time_budget=budget)
    return {"race": race, "cls": cls, "level": ch.level, "reached": res["reached"],
            "kills": res["kills"], "deaths": res["deaths"], "elapsed": res["elapsed"],
            "atk": ch.attack_power, "hp": ch.max_hp, "gold": ch.gold}


async def run_slice(start, count, target, budget, out):
    roster = _roster(N_TOTAL)[start:start + count]
    with open(out, "a", encoding="utf-8") as f:
        for race, cls in roster:
            r = await _run_one(race, cls, target, budget)
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            f.flush()
    print(f"slice {start}..{start+count}: готово ({len(roster)})")


def _avg(xs):
    return statistics.mean(xs) if xs else 0


def report(path, target):
    results = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    n = len(results)
    reached = [r for r in results if r["reached"]]
    print("=" * 64)
    print(f"  ПОПУЛЯЦИОННЫЙ ПРОГОН: {n} игроков -> уровень {target}")
    print("=" * 64)
    print(f"  Достигли {target} ур.: {len(reached)}/{n} ({100*len(reached)//n}%)")
    print(f"  Средний уровень: {_avg([r['level'] for r in results]):.1f}")
    print(f"  Убийств: всего {sum(r['kills'] for r in results)}, в среднем {_avg([r['kills'] for r in results]):.0f}")
    print(f"  Смертей: всего {sum(r['deaths'] for r in results)}, в среднем {_avg([r['deaths'] for r in results]):.1f}")
    print(f"  На {target} ур.: атака ~{_avg([r['atk'] for r in reached]):.0f}, HP ~{_avg([r['hp'] for r in reached]):.0f}, монет ~{_avg([r['gold'] for r in reached])/100:.0f}")
    print(f"  Время забега: среднее {_avg([r['elapsed'] for r in results]):.2f}с, макс {max(r['elapsed'] for r in results):.2f}с")
    def block(title, key, names):
        print(f"\n  -- По {title} (убийств / смертей / % до цели) --")
        grp = {}
        for r in results:
            grp.setdefault(r[key], []).append(r)
        for k in sorted(grp, key=lambda x: -_avg([y['kills'] for y in grp[x]])):
            g = grp[k]
            rr = sum(1 for x in g if x["reached"])
            print(f"    {names.get(k,k):14} ({len(g):2}): убийств {_avg([x['kills'] for x in g]):4.0f} · смертей {_avg([x['deaths'] for x in g]):4.1f} · дошли {100*rr//len(g):3}%")
    block("классам", "cls", {c: v.get("name", c) for c, v in CLASSES.items()})
    block("расам", "race", {r: v.get("name", r) for r, v in RACES.items()})
    print("=" * 64)


def main():
    a = sys.argv
    target = int(a[a.index("--to") + 1]) if "--to" in a else 50
    budget = float(a[a.index("--budget") + 1]) if "--budget" in a else 8.0
    if "--report" in a:
        report(a[a.index("--report") + 1], target)
    elif "--slice" in a:
        i = a.index("--slice")
        start = int(a[i + 1]); count = int(a[i + 2])
        out = a[a.index("--out") + 1] if "--out" in a else "/tmp/pop.jsonl"
        asyncio.run(run_slice(start, count, target, budget, out))
    else:
        n = int(a[a.index("--n") + 1]) if "--n" in a else N_TOTAL
        asyncio.run(run_slice(0, n, target, budget, "/tmp/pop.jsonl"))
        report("/tmp/pop.jsonl", target)


if __name__ == "__main__":
    main()
