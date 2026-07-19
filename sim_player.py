# -*- coding: utf-8 -*-
"""Безголовый симулятор «Семь Корон»: до LEVEL_CAP → реморт → снова до LEVEL_CAP.

Профили поведения (флаг политики выживания на ранних уровнях):
  • "smart"  — по умолчанию: пьёт зелья при низком HP, пополняет запас в городе,
               выбирает лучшего доступного моба. Модель адекватного игрока.
  • "naive"  — не покупает и не пьёт зелья, лупит ближайшего моба. Модель совсем
               зелёного новичка. Служит для оценки «пола» смертности до 10 ур.
"""
import asyncio, random, sys, time
from engine.content import WORLD, MOBS, ITEMS, HP_SCALE, validate
from engine.character import Character, LEVEL_CAP
from engine.world import World
from engine import combat, nav, money
from engine.loop import GameLoop

random.seed(42)

# ── политика профилей раннего онбординга ──
POTION_ITEM = "малое_зелье"
POTION_STOCK = 4          # сколько зелий держим/докупаем в городе (smart)
POTION_HP_FRAC = 0.55     # пьём, если HP упал ниже 55% максимума (smart)
# «потолок» уровня цели над своим: smart осторожен (+1), naive лезет в риск (+2).
# Мобы >+3 (красные) отсекаются везде — это уже верная гибель.
SMART_LEVEL_CAP = 1
NAIVE_LEVEL_CAP = 2


def _ff(world):
    now = time.time()
    for lst in world.mobs.values():
        for m in lst:
            if not m.alive:
                m.dead_at = now - 10_000
    world.process_respawns()


def _acceptable(ch, m, cap):
    """Моб не красный и не выше уровня игрока более чем на cap."""
    if not m.alive:
        return False
    if m.meta.get("level", 1) > ch.level + cap:
        return False
    return combat.mob_difficulty(ch.level, m.meta.get("level", 1)) != "red"


def _best_room(world, ch, cap=99):
    """Комната с самым «жирным» по опыту приемлемым мобом (в пределах cap)."""
    best, bx = None, -1
    for room, lst in world.mobs.items():
        for m in lst:
            if not _acceptable(ch, m, cap):
                continue
            if m.meta.get("xp", 0) > bx:
                bx, best = m.meta.get("xp", 0), room
    return best


def _best_mob(world, ch, room, cap=99):
    """Лучший по опыту приемлемый моб в комнате (smart-выбор, в пределах cap)."""
    c = [m for m in world.living_in(room) if _acceptable(ch, m, cap)]
    c.sort(key=lambda m: m.meta.get("xp", 0), reverse=True)
    return c[0] if c else None


def _nearest_mob(world, ch, room, cap=99):
    """Наивный выбор: первый живой приемлемый моб в комнате (без сортировки по xp)."""
    for m in world.living_in(room):
        if _acceptable(ch, m, cap):
            return m
    return None


def _restock_potions(ch):
    """Пополнить запас зелий (профиль smart «покупает» в городе)."""
    have = ch.inventory.count(POTION_ITEM)
    for _ in range(max(0, POTION_STOCK - have)):
        ch.inventory.append(POTION_ITEM)


def _drink_potion(ch) -> bool:
    """Выпить зелье лечения, если оно есть и HP просело. -> True если выпил."""
    if ch.hp > ch.max_hp * POTION_HP_FRAC:
        return False
    if POTION_ITEM not in ch.inventory:
        return False
    heal = ITEMS.get(POTION_ITEM, {}).get("effect", {}).get("heal", 0) * HP_SCALE
    ch.hp = min(ch.max_hp, ch.hp + heal)
    ch.inventory.remove(POTION_ITEM)
    return True


def _step_toward(ch, room):
    path = nav.bfs_path(ch.room, lambda r, tr=room: r == tr)
    if path:
        nxt = WORLD[ch.room]["exits"].get(path[0])
        if nxt:
            ch.room = nxt


async def simulate_to(ch, world, gl, target, max_steps=2_000_000, time_budget=120,
                      profile="smart"):
    kills = deaths = steps = 0
    potions_drunk = 0
    visited = set()
    level_events = []
    last = ch.level
    smart = (profile == "smart")
    cap = SMART_LEVEL_CAP if smart else NAIVE_LEVEL_CAP
    pick = _best_mob if smart else _nearest_mob
    if smart:
        _restock_potions(ch)          # выходим в мир с полным запасом зелий
    start = time.time()
    while ch.level < target and steps < max_steps:
        steps += 1
        if time.time() - start > time_budget:
            break
        visited.add(ch.room)
        # smart докупает зелья в безопасной/отдыхающей комнате, если запас иссяк
        if smart and (WORLD.get(ch.room, {}).get("safe") or WORLD.get(ch.room, {}).get("rest")) \
                and ch.inventory.count(POTION_ITEM) < 2:
            _restock_potions(ch)
        mob = pick(world, ch, ch.room, cap)
        if not mob:
            _ff(world)
            tr = _best_room(world, ch, cap) or _best_room(world, ch)
            if tr is None:
                break
            if tr != ch.room:
                _step_toward(ch, tr)
                continue
            mob = pick(world, ch, ch.room, cap) or _nearest_mob(world, ch, ch.room)
            if not mob:
                break
        if steps % 40 == 0:
            br = _best_room(world, ch, cap) or _best_room(world, ch)
            if br and br != ch.room:
                _step_toward(ch, br)
                continue
        ch.target = mob.key
        if 1 not in mob.aggro:
            mob.aggro.append(1)
        # smart лечится ПЕРЕД боем, если вошёл в него подраненным
        if smart and _drink_potion(ch):
            potions_drunk += 1
        r = 0
        while mob.alive and ch.hp > 0 and r < 80:
            r += 1
            combat.player_basic_attack(ch, mob)
            if mob.hp <= 0:
                break
            combat.tick_effects_mob(mob)
            if mob.hp <= 0:
                break
            if not combat.mob_is_disabled(mob):
                combat.mob_attack(mob, ch)
            # smart-новичок глотает зелье, когда HP просел (naive — никогда)
            if smart and ch.hp > 0 and _drink_potion(ch):
                potions_drunk += 1
            if ch.hp <= 0:
                break
        if ch.hp <= 0:
            deaths += 1
            ch.hp = ch.max_hp; ch.mp = ch.start_resource()
            ch.effects = []; ch.target = None; mob.aggro = []
            continue
        if mob.hp <= 0:
            await gl.on_mob_death(mob, [ch])
            kills += 1
            if ch.level > last:
                level_events.append((ch.level, kills, ch.room)); last = ch.level
            ch.hp = ch.max_hp; ch.mp = ch.start_resource()
            ch.effects = []; ch.target = None
    return {"kills": kills, "deaths": deaths, "steps": steps, "visited": visited,
            "level_events": level_events, "elapsed": time.time() - start,
            "reached": ch.level >= target, "potions": potions_drunk, "profile": profile}


async def build(remort_count=0):
    validate()
    world = World()
    ch = Character(uid=1, name="Тестиус", cls="warrior", race="human")
    if remort_count:
        ch.flags["remort"] = remort_count
    ch.init_vitals()
    async def send(uid, text): pass
    async def save(c): pass
    gl = GameLoop(world, {1: ch}, send, save)
    gl.on_combat_reward = None
    return ch, world, gl


def _report(title, ch, res):
    print(f"\n── {title} ──")
    prof = res.get("profile")
    if prof:
        print(f"  Профиль: {prof}")
    print(f"  Уровень {ch.level} | опыт {ch.xp}/{ch.xp_to_next} | "
          f"{'ЦЕЛЬ ✅' if res['reached'] else 'не достиг ❌'}")
    print(f"  Убийств: {res['kills']} | смертей: {res['deaths']} | "
          f"зелий выпито: {res.get('potions', 0)} | "
          f"шагов: {res['steps']} | время: {res['elapsed']:.1f}с")
    print(f"  Атака: {ch.attack_power} | HP: {ch.max_hp} | золото: {money.fmt(ch.gold)} | "
          f"локаций: {len(res['visited'])}")
    ms = [e for e in res['level_events'] if e[0] in (10, 25, 40, LEVEL_CAP)]
    if ms:
        print("  Вехи: " + "  ".join(f"ур.{l}←убийство#{k}" for l, k, _ in ms))


async def run_remort():
    print("═" * 60)
    print(f"  СИМУЛЯЦИЯ: до {LEVEL_CAP} → РЕМОРТ → снова до {LEVEL_CAP}")
    print("═" * 60)
    ch, world, gl = await build()
    print(f"Персонаж: {ch.name} (человек-воин), старт ур.{ch.level}, атака {ch.attack_power}")
    res1 = await simulate_to(ch, world, gl, LEVEL_CAP, time_budget=120)
    _report(f"ЗАБЕГ 1 (до {LEVEL_CAP})", ch, res1)
    atk1, hp1 = ch.attack_power, ch.max_hp
    ok = ch.remort()
    print(f"\n🌟 РЕМОРТ: {'выполнен' if ok else 'НЕ удался'} → реморт №{ch.remort_count}, "
          f"уровень сброшен до {ch.level}, навсегда +{int(ch.remort_bonus*100)}% к силе/HP.")
    print(f"   На 1 ур. после реморта: атака {ch.attack_power}, HP {ch.max_hp}")
    ch2, world2, gl2 = await build(remort_count=ch.remort_count)
    res2 = await simulate_to(ch2, world2, gl2, LEVEL_CAP, time_budget=120)
    _report(f"ЗАБЕГ 2 (после реморта, до {LEVEL_CAP})", ch2, res2)
    print("\n" + "═" * 60)
    print("  СРАВНЕНИЕ")
    print("═" * 60)
    print(f"  Забег 1 (0 ремортов): {res1['kills']} убийств | финал: атака {atk1}, HP {hp1}")
    print(f"  Забег 2 (1 реморт):   {res2['kills']} убийств | финал: атака {ch2.attack_power}, HP {ch2.max_hp}")
    print(f"  Разница в атаке на {LEVEL_CAP} ур.: +{ch2.attack_power - atk1} ({int(ch2.remort_bonus*100)}% реморт-бонус)")
    print("═" * 60)


async def run_profile(target, profile):
    ch, world, gl = await build()
    res = await simulate_to(ch, world, gl, target, time_budget=120, profile=profile)
    _report(f"ЗАБЕГ до {target} [{profile}]", ch, res)
    return res


def main():
    if "--remort" in sys.argv:
        asyncio.run(run_remort()); return
    target = 10
    if "--to" in sys.argv:
        target = int(sys.argv[sys.argv.index("--to") + 1])
    # выбор профиля: --naive / --smart прогоняет один; без флага — оба, для сравнения
    only = None
    if "--naive" in sys.argv:
        only = "naive"
    elif "--smart" in sys.argv:
        only = "smart"

    async def run():
        if only:
            await run_profile(target, only)
            return
        print("═" * 60)
        print(f"  ОНБОРДИНГ: сравнение профилей до {target} ур.")
        print("═" * 60)
        rs = await run_profile(target, "smart")
        rn = await run_profile(target, "naive")
        print("\n" + "═" * 60)
        print("  ИТОГ ОНБОРДИНГА (смертей до цели)")
        print("═" * 60)
        print(f"  smart (пьёт зелья):   {rs['deaths']} смертей  "
              f"(цель ≤3 — {'OK' if rs['deaths'] <= 3 else 'НЕ ДОТЯГИВАЕТ'})")
        print(f"  naive (без зелий):    {rn['deaths']} смертей  "
              f"(цель ≤6 — {'OK' if rn['deaths'] <= 6 else 'НЕ ДОТЯГИВАЕТ'})")
        print("═" * 60)
    asyncio.run(run())


if __name__ == "__main__":
    main()
