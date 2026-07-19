# -*- coding: utf-8 -*-
"""Сравнение старого и гибридного (rules2) ядра на одинаковом прогоне."""
import asyncio, random, time
from engine import rules2, combat, money
from engine.character import Character
from engine.world import World
from engine.loop import GameLoop
import sim_player as sp


async def run_core(enabled, target=30, seed=42):
    rules2.ENABLED = enabled
    random.seed(seed)
    ch, world, gl = await sp.build()
    res = await sp.simulate_to(ch, world, gl, target, time_budget=60)
    rules2.ENABLED = False
    return ch, res


async def main():
    target = 30
    print("═" * 60)
    print(f"  СРАВНЕНИЕ ЯДЕР: воин-человек до {target} уровня (seed=42)")
    print("═" * 60)
    ch_old, r_old = await run_core(False, target)
    ch_new, r_new = await run_core(True, target)

    def row(label, ch, r):
        return (f"  {label:18} ур.{ch.level:>3} | убийств {r['kills']:>5} | "
                f"смертей {r['deaths']:>3} | раундов-боя* {r['steps']:>5} | {r['elapsed']:.1f}с")

    print(row("Старое ядро", ch_old, r_old))
    print(row("Гибрид rules2", ch_new, r_new))
    dk = r_new["kills"] - r_old["kills"]
    print("-" * 60)
    print(f"  Δ убийств до цели: {dk:+d}  "
          f"({'быстрее — мультиатака' if dk < 0 else 'медленнее — резисты режут урон' if dk > 0 else 'без разницы'})")
    print("  * resist/immune мобов и мультиатака влияют на скорость фарма;")
    print("    нежить/големы теперь иммунны к части урона, мультиатака ускоряет воина.")
    print("═" * 60)


if __name__ == "__main__":
    asyncio.run(main())
