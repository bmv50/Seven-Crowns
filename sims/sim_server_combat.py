"""Замер серверного боя: 3 последовательных схватки, без зелий/рестока/автолечения.

Не модель всей экономики/прокачки: фиксированный уровень, базовый лоудаут,
стандартные оружие/броня того же тира из sim_rules2. Награды/левелапы не выдаём.
Сравниваем ввод раз в секунду и 20 попыток/сек на одинаковых seed.
Одинаковый результат означает отсутствие преимущества от спама, не готовый баланс.
Запуск из корня: python -m sims.sim_server_combat --runs 10
"""
import argparse
import json
import random
import statistics

from engine import combat, rules2
from engine.content import CLASSES, MOBS, SKILLS, WORLD
from engine.world import World, MobInstance
from engine.interaction import ActionPacer, PlayerClock
from sims.sim_rules2 import build_char


def _action(ch, mob, world):
    ready = [s for s in ch.skills if ch.cooldowns.get(s, 0) <= 0 and ch.mp >= SKILLS[s]["mp"]]
    if ch.hp < 0.6 * ch.max_hp:
        healing = [s for s in ready if SKILLS[s]["kind"] == "heal"]
        if healing:
            return combat.use_skill(ch, healing[0], world, [ch])[0]
    attacks = sorted((s for s in ready if SKILLS[s]["kind"] == "damage"),
                     key=lambda s: SKILLS[s]["scaling"], reverse=True)
    if attacks:
        return combat.use_skill(ch, attacks[0], world, [ch])[0]
    combat.player_basic_attack(ch, mob)
    return True


def run_chain(cls, level, seed, spam=False):
    random.seed(seed)
    ch = build_char(cls, level, 1)
    ch.init_vitals()  # убираем огромный HP DPS-манекена, только один раз за цепочку
    ch.init_skills()
    ch.inventory = []
    room = next(iter(WORLD))
    ch.room = room
    world = World()
    world.mobs = {room: []}
    pool = sorted(mid for mid, meta in MOBS.items() if not meta.get("boss")
                  and abs(int(meta.get("level", 1)) - level) <= 1)
    if not pool:
        raise ValueError(f"Нет обычных целей для уровня {level}")
    now = [0.0]
    clock = PlayerClock(clock=lambda: now[0])
    pacer = ActionPacer(clock=lambda: now[0])
    clock.advance(ch)
    kills = actions = winds = 0
    step = 0
    for fight in range(3):
        mob = MobInstance(str(fight), pool[seed % len(pool)], room)
        world.mobs[room] = [mob]
        mob.last_tick = now[0]
        ch.target = mob.key
        mob.aggro = [1]
        # Моб использует собственный tick_speed; игрок — общий серверный бюджет.
        end = step + 3600  # не больше 180 секунд на бой
        while ch.hp > 0 and mob.hp > 0 and step < end:
            now[0] = step / 20
            if step % 20 == 0:
                clock.advance(ch)
            if spam or step % 20 == 0:
                clock.advance(ch)
                if pacer.acquire(1) == 0:
                    actions += _action(ch, mob, world)
            if mob.hp > 0 and now[0] - mob.last_tick >= mob.meta.get("tick_speed", 4):
                mob.last_tick = now[0]
                combat.tick_effects_mob(mob)
                if mob.hp > 0:
                    if combat.mob_is_disabled(mob):
                        combat.clear_windup(mob)
                    elif combat.is_winding_up(mob):
                        combat.clear_windup(mob)
                        combat.mob_attack(mob, ch, heavy=True)
                    elif combat.telegraph_due(mob):
                        combat.start_windup(mob)
                        winds += 1
                    else:
                        combat.mob_attack(mob, ch)
            step += 1
        if mob.hp > 0 or ch.hp <= 0:
            break
        kills += 1
        world.kill(mob)
        ch.target = None
        ch.reset_combat_resource()
        # HP, MP, эффекты и откаты НЕ восстанавливаем после победы.
    return {"kills": kills, "actions": actions, "seconds": round(step / 20, 2),
            "hp": ch.hp, "mp": ch.mp, "windups": winds}


def measure(runs=10, levels=(1, 6, 15, 25)):
    rules2.ENABLED = True
    combat.ENABLED_TELEGRAPH = True
    results = []
    for level in levels:
        for cls in CLASSES:
            samples = []
            for seed in range(42, 42 + runs):
                regular = run_chain(cls, level, seed)
                fast = run_chain(cls, level, seed, spam=True)
                assert regular == fast, (cls, level, seed, regular, fast)
                samples.append(regular)
            results.append({"class": cls, "level": level, "runs": runs,
                            "three_wins": sum(s["kills"] == 3 for s in samples),
                            "avg_kills": round(statistics.mean(s["kills"] for s in samples), 2),
                            "avg_seconds": round(statistics.mean(s["seconds"] for s in samples), 2),
                            "spam_advantage": False})
    return results


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--runs", type=int, default=10)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    if args.runs <= 0:
        p.error("--runs должен быть положительным")
    results = measure(args.runs)
    if not args.json:
        print("Ур. Класс           3 победы    Ср. убийств  Ср. секунд")
        for r in results:
            print(f"{r['level']:>3} {r['class']:<15} {r['three_wins']:>2}/{r['runs']:<3} "
                  f"{r['avg_kills']:>12.2f} {r['avg_seconds']:>11.2f}")
        print("OK: обычный ввод и 20 попыток/сек дают одинаковое состояние во всех прогонах")
    print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()
