# -*- coding: utf-8 -*-
"""Онбординг-симулятор «Семь Корон»: прогон КАЖДОГО из 6 классов до 10 уровня.

Расширение sim_player на все классы и на РЕАЛЬНОЕ применение умений/ресурса.
Детерминирован по seed (глобальный random сидируется перед каждым прогоном).

Два профиля поведения новичка (см. регламент Этапа 4.1 и решение лида в 4.2):
  • "smart" — адекватный игрок: применяет доступные по уровню боевые скиллы,
     когда хватает ресурса (маг/жрец/некромант/паладин — кастуют; воин копит
     ярость и раскрывает Мощный удар; разбойник тратит энергию), лечится своими
     скиллами (жрец/паладин) и зельями (<40% HP), докупает зелья в городе, учит
     открывшиеся умения у наставника (если по карману), цели ≤ level+1.
  • "naive" (МОДЕЛЬ v2: РЕДКИЕ КАСТЫ, Этап 4.2) — зелёный игрок: в основном
     базовая атака, но нажимает боевой скилл, когда ресурс заполнен ≥80%
     (NAIVE_CAST_RESOURCE_FRAC) — так, как учит туториал на 3-м шаге «skill».
     Берёт первый доступный боевой скилл лоудаута (без выбора «лучшего»), НЕ
     лечится скиллами, НЕ ставит баффы, зелья по-прежнему НЕ докупает (пьёт
     только стартовые при <30% HP), лезет к целям ≤ level+2. Решение лида по
     итогам 4.1 (§7, опция 1 docs/BALANCE_ONBOARDING.md): модель, где игрок ни
     разу за 10 уровней не нажал светящуюся кнопку скилла (хотя её показывает
     сам туториал), нереалистична. Оценивает реалистичный «пол» смертности.

Метрики на класс×профиль: смерти, убийства, шаги, «время» (шаги×4с), зелья
лечения, зелья маны. Вывод — таблица + JSON-строка (машинный парсинг).

Флаги:
  --class NAME     только один класс (warrior/mage/rogue/priest/paladin/necromancer)
  --profile P      только один профиль (smart/naive)
  --seed N         базовый seed (по умолчанию 42)
  --runs N         сколько прогонов усреднять (по умолчанию 3; seeds = N..N+runs-1)
  --to L           целевой уровень (по умолчанию 10)
  --json           печатать только JSON-строку (для парсинга)

Мировые данные и движок НЕ трогаются — это ЧИСТО измерительный инструмент.
Стартовый набор берётся из engine/starter.py (единый источник правды с ботом).
"""
import asyncio
import json
import random
import statistics
import sys
import time

from engine.content import WORLD, ITEMS, SKILLS, HP_SCALE, validate
from engine.character import Character
from engine.world import World
from engine import combat, nav, skills as skillmod, starter
from engine.loop import GameLoop

# ── порядок классов фиксирован (стабильный вывод/сравнение разброса) ──
ALL_CLASSES = ["warrior", "mage", "rogue", "priest", "paladin", "necromancer"]
PROFILES = ["smart", "naive"]

# ── политика профилей ──
SMART_LEVEL_CAP = 1        # smart осторожен: цели не выше level+1
NAIVE_LEVEL_CAP = 2        # naive рискует: до level+2 (красные >+3 отсекаются всегда)
SMART_HP_FRAC = 0.40       # smart пьёт зелье лечения при HP < 40%
NAIVE_HP_FRAC = 0.30       # naive пьёт стартовое зелье при HP < 30%
HEAL_FRAC = 0.60           # smart-лекарь (жрец/паладин) кастует лечение при HP < 60%
BUFF_FRAC = 0.45           # smart ставит защитный бафф-панику при HP < 45%
ROUND_CAP = 100            # предохранитель от зависания в одном бою
STEP_SECONDS = 4           # «время хода» игрока для оценки длительности сессии

# naive v2 (Этап 4.2, решение лида): порог ресурса для редкого каста боевого
# скилла. Ресурс стартует бой ПОЛНЫМ у всех классов, кроме ярости воина (копится
# от ударов) — поэтому маг/жрец/паладин/некромант/разбойник обычно откроют бой
# одним кастом, а воин раскроется по ходу боя, когда ярость нагонит порог.
NAIVE_CAST_RESOURCE_FRAC = 0.80

HP_POTION = starter.HP_POTION
MANA_POTION = starter.MANA_POTION

# цели регламента
GOAL_SMART_DEATHS = 3
GOAL_NAIVE_DEATHS = 6
GOAL_SPREAD = 0.30         # разброс шагов между классами внутри профиля ≤30% медианы


# ═════════════════════ навигация и выбор цели (как в sim_player) ═════════════════════
def _ff(world):
    """Форс-респавн всех павших мобов (стартовые зоны не должны пустеть)."""
    now = time.time()
    for lst in world.mobs.values():
        for m in lst:
            if not m.alive:
                m.dead_at = now - 10_000
    world.process_respawns()


def _acceptable(ch, m, cap):
    """Моб жив, не выше уровня игрока более чем на cap и не «красный»."""
    if not m.alive:
        return False
    if m.meta.get("level", 1) > ch.level + cap:
        return False
    return combat.mob_difficulty(ch.level, m.meta.get("level", 1)) != "red"


def _best_room(world, ch, cap=99):
    """Комната с самым «жирным» по опыту приемлемым мобом."""
    best, bx = None, -1
    for room, lst in world.mobs.items():
        for m in lst:
            if not _acceptable(ch, m, cap):
                continue
            if m.meta.get("xp", 0) > bx:
                bx, best = m.meta.get("xp", 0), room
    return best


def _best_mob(world, ch, room, cap=99):
    """Лучший по опыту приемлемый моб в комнате (smart)."""
    c = [m for m in world.living_in(room) if _acceptable(ch, m, cap)]
    c.sort(key=lambda m: m.meta.get("xp", 0), reverse=True)
    return c[0] if c else None


def _nearest_mob(world, ch, room, cap=99):
    """Первый живой приемлемый моб (naive — без выбора по опыту)."""
    for m in world.living_in(room):
        if _acceptable(ch, m, cap):
            return m
    return None


def _step_toward(ch, room):
    path = nav.bfs_path(ch.room, lambda r, tr=room: r == tr)
    if path:
        nxt = WORLD[ch.room]["exits"].get(path[0])
        if nxt:
            ch.room = nxt


# ═════════════════════ расходники ═════════════════════
def _restock(ch, cls):
    """Пополнить зелья до стартового набора класса (smart «покупает» в городе)."""
    for _ in range(max(0, starter.hp_potion_count(cls) - ch.inventory.count(HP_POTION))):
        ch.inventory.append(HP_POTION)
    for _ in range(max(0, starter.mana_potion_count(cls) - ch.inventory.count(MANA_POTION))):
        ch.inventory.append(MANA_POTION)


def _drink_hp_potion(ch, smart) -> bool:
    """Выпить зелье лечения при просевшем HP. -> True если выпил."""
    frac = SMART_HP_FRAC if smart else NAIVE_HP_FRAC
    if ch.hp > ch.max_hp * frac:
        return False
    if HP_POTION not in ch.inventory:
        return False
    heal = ITEMS.get(HP_POTION, {}).get("effect", {}).get("heal", 0) * HP_SCALE
    ch.hp = min(ch.max_hp, ch.hp + heal)
    ch.inventory.remove(HP_POTION)
    return True


def _drink_mana_potion(ch) -> bool:
    """Выпить зелье маны (мана НЕ масштабируется HP_SCALE). -> True если выпил."""
    if MANA_POTION not in ch.inventory:
        return False
    restore = ITEMS.get(MANA_POTION, {}).get("effect", {}).get("mana", 0)
    ch.mp = min(ch.max_resource, ch.mp + restore)
    ch.inventory.remove(MANA_POTION)
    return True


# ═════════════════════ выбор действия smart-профиля ═════════════════════
def _has_active_defense(ch) -> bool:
    return any(e.get("type") in ("shield", "dodge") and e.get("turns", 0) > 0
               for e in ch.effects)


def _castable(ch, sid) -> bool:
    """Скилл готов: не на кулдауне и хватает ресурса (мана/ярость/энергия = ch.mp)."""
    return ch.cooldowns.get(sid, 0) <= 0 and ch.mp >= SKILLS[sid]["mp"]


def _smart_action(ch, mob, world, party, stats) -> bool:
    """Выбор умного действия. -> True если умение израсходовало ход (иначе базовая атака).

    Приоритет: экстренное лечение → паника-щит → лучший боевой скилл → (кастеру)
    глоток зелья маны и повторный каст. Воину на первых ходах ресурса (ярости) не
    хватает — вернёт False и он копит ярость базовыми ударами, раскрывая скиллы позже.
    """
    loadout = ch.skills
    heals = [s for s in loadout if SKILLS[s]["kind"] == "heal"]
    dmgs = [s for s in loadout if SKILLS[s]["kind"] == "damage"]
    buffs = [s for s in loadout if SKILLS[s]["kind"] == "buff"
             and SKILLS[s].get("effect", {}).get("type") in ("shield", "dodge")]

    # 1) экстренное лечение своим скиллом (жрец/паладин)
    if heals and ch.hp < HEAL_FRAC * ch.max_hp:
        for s in sorted(heals, key=lambda s: SKILLS[s]["scaling"], reverse=True):
            if _castable(ch, s):
                ok, _ = combat.use_skill(ch, s, world, party)
                if ok:
                    return True

    # 2) паника-щит: нет активной защиты и HP просел — прикрыться баффом
    if buffs and ch.hp < BUFF_FRAC * ch.max_hp and not _has_active_defense(ch):
        for s in buffs:
            if _castable(ch, s):
                ok, _ = combat.use_skill(ch, s, world, party)
                if ok:
                    return True

    # 3) лучший доступный боевой скилл (по scaling)
    aff = [s for s in dmgs if _castable(ch, s)]
    if aff:
        aff.sort(key=lambda s: SKILLS[s]["scaling"], reverse=True)
        ok, _ = combat.use_skill(ch, aff[0], world, party)
        if ok:
            return True

    # 4) кастер застрял без маны — глотнуть зелье маны и попробовать каст снова
    if ch.resource_type == "mana" and dmgs and MANA_POTION in ch.inventory:
        cheapest = min(SKILLS[s]["mp"] for s in dmgs)
        if ch.mp < cheapest and _drink_mana_potion(ch):
            stats["mana_potions"] += 1
            aff = [s for s in dmgs if _castable(ch, s)]
            if aff:
                aff.sort(key=lambda s: SKILLS[s]["scaling"], reverse=True)
                ok, _ = combat.use_skill(ch, aff[0], world, party)
                if ok:
                    return True
    return False


def _naive_action(ch, mob, world, party) -> bool:
    """naive v2 (Этап 4.2, решение лида по итогам 4.1 §7 опция 1): даже совсем
    зелёный игрок хоть иногда нажимает светящуюся боевую кнопку — так учит
    туториал на шаге «skill». Кастует ТОЛЬКО когда ресурс заполнен ≥80%
    (NAIVE_CAST_RESOURCE_FRAC), без выбора «лучшего» умения — первый доступный
    боевой скилл лоудаута, который проходит по кулдауну/ресурсу. НЕ лечится
    скиллами, НЕ ставит баффы, НЕ докупает и не пьёт зелья маны — во всём
    остальном профиль naive не меняется. -> True, если умение израсходовало
    ход (иначе вызывающий код бьёт базовой атакой)."""
    if ch.max_resource <= 0 or ch.mp < NAIVE_CAST_RESOURCE_FRAC * ch.max_resource:
        return False
    dmgs = [s for s in ch.skills if SKILLS[s]["kind"] == "damage"]
    for s in dmgs:
        if _castable(ch, s):
            ok, _ = combat.use_skill(ch, s, world, party)
            if ok:
                return True
    return False


def _train_skills(ch):
    """smart учит открывшиеся по уровню умения у наставника (если хватает золота)."""
    for s in skillmod.learnable_now(ch):
        skillmod.learn(ch, s)   # learn сам проверит цену и авто-слотит в лоудаут


# ═════════════════════ ядро симуляции ═════════════════════
async def simulate_to(ch, world, gl, target, cls, profile,
                      max_steps=500_000, time_budget=120):
    smart = (profile == "smart")
    cap = SMART_LEVEL_CAP if smart else NAIVE_LEVEL_CAP
    pick = _best_mob if smart else _nearest_mob
    stats = {"kills": 0, "deaths": 0, "steps": 0, "potions": 0, "mana_potions": 0}
    # диагностика для выбора рычага: смертей на каждом уровне (level -> deaths).
    # пик на 5–7 ур. подсказывает щит/наборы; ровное распределение — hp-базу.
    deaths_by_level = {}
    party = [ch]
    last = ch.level
    if smart:
        _restock(ch, cls)                 # выходим в мир с полным набором
    start = time.time()
    while ch.level < target and stats["steps"] < max_steps:
        stats["steps"] += 1
        if time.time() - start > time_budget:
            break
        # smart докупает зелья в безопасной/отдыхающей комнате при иссякшем запасе
        if smart and (WORLD.get(ch.room, {}).get("safe") or WORLD.get(ch.room, {}).get("rest")) \
                and (ch.inventory.count(HP_POTION) < 2
                     or ch.inventory.count(MANA_POTION) < starter.mana_potion_count(cls)):
            _restock(ch, cls)
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
        if stats["steps"] % 40 == 0:
            br = _best_room(world, ch, cap) or _best_room(world, ch)
            if br and br != ch.room:
                _step_toward(ch, br)
                continue
        ch.target = mob.key
        if 1 not in mob.aggro:
            mob.aggro.append(1)
        # smart лечится перед боем, если вошёл подраненным
        if smart and _drink_hp_potion(ch, True):
            stats["potions"] += 1
        r = 0
        while mob.alive and ch.hp > 0 and r < ROUND_CAP:
            r += 1
            # 1) ход игрока: skill (smart — полностью; naive v2 — редко, ≥80%
            # ресурса, см. _naive_action) или базовая атака
            if smart:
                used = _smart_action(ch, mob, world, party, stats)
            else:
                used = _naive_action(ch, mob, world, party)
            if not used:
                combat.player_basic_attack(ch, mob)
            if mob.hp <= 0:
                break
            # 2) DoT-эффекты на мобе (яд/горение/кровотечение)
            combat.tick_effects_mob(mob)
            if mob.hp <= 0:
                break
            # 3) конец хода игрока: тик кулдаунов/баффов + реген ресурса (как в бою бота)
            combat.advance_player_turn(ch)
            # 4) ответ моба (если не заморожен/оглушён)
            if not combat.mob_is_disabled(mob):
                combat.mob_attack(mob, ch)
            # 5) добор зелья по HP (smart <40%, naive <30%)
            if ch.hp > 0 and _drink_hp_potion(ch, smart):
                stats["potions"] += 1
            if ch.hp <= 0:
                break
        if ch.hp <= 0:
            stats["deaths"] += 1
            deaths_by_level[ch.level] = deaths_by_level.get(ch.level, 0) + 1
            ch.hp = ch.max_hp
            ch.mp = ch.start_resource()
            ch.effects = []
            ch.cooldowns = {}
            ch.target = None
            mob.aggro = []
            continue
        if mob.hp <= 0:
            await gl.on_mob_death(mob, [ch])
            stats["kills"] += 1
            if ch.level > last:
                last = ch.level
                if smart:
                    _train_skills(ch)      # у наставника — открывшиеся умения
            ch.hp = ch.max_hp
            ch.mp = ch.start_resource()
            ch.effects = []
            ch.cooldowns = {}
            ch.target = None
    return {**stats, "reached": ch.level >= target, "level": ch.level,
            "elapsed": time.time() - start, "deaths_by_level": deaths_by_level}


async def build(cls):
    """Свежий человек-<cls> со стартовым набором из engine/starter.py."""
    world = World()
    ch = Character(uid=1, name="Тестиус", cls=cls, race="human")
    ch.init_vitals()
    ch.init_skills()
    ch.inventory = list(starter.starting_consumables(cls))

    async def send(uid, text):
        pass

    async def save(c):
        pass

    gl = GameLoop(world, {1: ch}, send, save)
    gl.on_combat_reward = None
    return ch, world, gl


async def run_once(cls, profile, seed, target=10):
    """Один детерминированный прогон -> словарь метрик."""
    random.seed(seed)
    ch, world, gl = await build(cls)
    res = await simulate_to(ch, world, gl, target, cls, profile)
    res.update({"cls": cls, "profile": profile, "seed": seed,
                "attack": ch.attack_power, "max_hp": ch.max_hp, "gold": ch.gold})
    return res


async def run_combo(cls, profile, seeds, target=10):
    """Комбо класс×профиль, усреднённое по нескольким seed."""
    runs = [await run_once(cls, profile, s, target) for s in seeds]

    def avg(k):
        return sum(r[k] for r in runs) / len(runs)

    # суммарное распределение смертей по уровням за все прогоны (диагностика рычага):
    # ключи-строки для чистой JSON-сериализации, порядок — по возрастанию уровня.
    dbl = {}
    for r in runs:
        for lvl, d in (r.get("deaths_by_level") or {}).items():
            dbl[int(lvl)] = dbl.get(int(lvl), 0) + d
    deaths_by_level = {str(k): dbl[k] for k in sorted(dbl)}

    return {
        "cls": cls, "profile": profile, "runs": len(runs), "seeds": list(seeds),
        "deaths": round(avg("deaths"), 2), "kills": round(avg("kills"), 1),
        "steps": round(avg("steps"), 1), "potions": round(avg("potions"), 1),
        "mana_potions": round(avg("mana_potions"), 1),
        "time_min": round(avg("steps") * STEP_SECONDS / 60.0, 1),
        "max_deaths": max(r["deaths"] for r in runs),
        "reached_all": all(r["reached"] for r in runs),
        "deaths_by_level": deaths_by_level,
        "max_hp": runs[-1]["max_hp"], "attack": runs[-1]["attack"],
    }


# ═════════════════════ анализ и вывод ═════════════════════
def _spread(steps_by_class):
    """Разброс шагов между классами = (max-min)/median. -> (spread, median, min, max)."""
    vals = list(steps_by_class.values())
    med = statistics.median(vals)
    lo, hi = min(vals), max(vals)
    spread = (hi - lo) / med if med else 0.0
    return spread, med, lo, hi


def _analyze(results, classes, profiles, target):
    """Собрать сводку по целям регламента (для отчёта и JSON)."""
    summary = {"target": target, "profiles": {}}
    for p in profiles:
        combos = {c: results[f"{c}:{p}"] for c in classes if f"{c}:{p}" in results}
        steps_by = {c: combos[c]["steps"] for c in combos}
        spread, med, lo, hi = _spread(steps_by) if steps_by else (0, 0, 0, 0)
        goal_deaths = GOAL_SMART_DEATHS if p == "smart" else GOAL_NAIVE_DEATHS
        worst_deaths = max((combos[c]["deaths"] for c in combos), default=0)
        worst_max_deaths = max((combos[c]["max_deaths"] for c in combos), default=0)
        all_reached = all(combos[c]["reached_all"] for c in combos)
        summary["profiles"][p] = {
            "spread": round(spread, 3), "median_steps": med,
            "min_steps": lo, "max_steps": hi,
            "goal_deaths": goal_deaths, "worst_avg_deaths": round(worst_deaths, 2),
            "worst_single_deaths": worst_max_deaths,
            "all_reached": all_reached,
            "goal_reached_ok": all_reached,
            "goal_deaths_ok": worst_deaths <= goal_deaths,
            "goal_spread_ok": spread <= GOAL_SPREAD,
        }
    return summary


def _print_report(results, classes, profiles, target, json_only):
    summary = _analyze(results, classes, profiles, target)
    if not json_only:
        print("═" * 78)
        print(f"  ОНБОРДИНГ-СИМУЛЯТОР: 6 классов × 2 профиля, цель — уровень {target}")
        print("═" * 78)
        for p in profiles:
            print(f"\n── Профиль: {p.upper()} "
                  f"(цель смертей ≤{GOAL_SMART_DEATHS if p=='smart' else GOAL_NAIVE_DEATHS}) ──")
            print(f"  {'класс':<12}{'смерти':>8}{'убийств':>9}{'шагов':>8}"
                  f"{'время,мин':>11}{'зелья':>7}{'мана':>6}{'ур.10':>7}")
            for c in classes:
                key = f"{c}:{p}"
                if key not in results:
                    continue
                r = results[key]
                reached = "да" if r["reached_all"] else "НЕТ"
                print(f"  {c:<12}{r['deaths']:>8}{r['kills']:>9}{r['steps']:>8}"
                      f"{r['time_min']:>11}{r['potions']:>7}{r['mana_potions']:>6}{reached:>7}")
            # диагностика: распределение смертей по уровням (для выбора рычага).
            # печатаем только классы, где кто-то умирал — чтобы увидеть пик.
            dist_lines = []
            for c in classes:
                key = f"{c}:{p}"
                dbl = results.get(key, {}).get("deaths_by_level") or {}
                if dbl:
                    dist = " ".join(f"ур{lvl}:{n}" for lvl, n in
                                    sorted(dbl.items(), key=lambda kv: int(kv[0])))
                    dist_lines.append(f"    {c:<12} {dist}")
            if dist_lines:
                print(f"  смерти по уровням (сумма за {list(results.values())[0]['runs']} прогонов):")
                for ln in dist_lines:
                    print(ln)
            s = summary["profiles"][p]
            print(f"  разброс шагов между классами: {s['spread']*100:.1f}% "
                  f"(медиана {s['median_steps']:.0f}, min {s['min_steps']:.0f}, "
                  f"max {s['max_steps']:.0f}) — цель ≤{int(GOAL_SPREAD*100)}%: "
                  f"{'OK' if s['goal_spread_ok'] else 'ПРОВАЛ'}")
            print(f"  худшая смертность (avg): {s['worst_avg_deaths']} — "
                  f"{'OK' if s['goal_deaths_ok'] else 'ПРОВАЛ'};  "
                  f"все достигли {target}: {'OK' if s['all_reached'] else 'ПРОВАЛ'}")
        print("\n" + "═" * 78)
        print("  ИТОГ ПО ЦЕЛЯМ РЕГЛАМЕНТА")
        print("═" * 78)
        for p in profiles:
            s = summary["profiles"][p]
            ok = s["goal_reached_ok"] and s["goal_deaths_ok"] and s["goal_spread_ok"]
            print(f"  {p:<6}: достигли10={'OK' if s['goal_reached_ok'] else 'НЕТ'}  "
                  f"смерти={'OK' if s['goal_deaths_ok'] else 'НЕТ'}  "
                  f"разброс={'OK' if s['goal_spread_ok'] else 'НЕТ'}  ⇒ "
                  f"{'ВСЕ ЦЕЛИ ✅' if ok else 'ЕСТЬ ПРОВАЛ ❌'}")
        print("═" * 78)
    # JSON-строка для машинного парсинга
    payload = {"target": target, "combos": results, "summary": summary}
    print("SIM_JSON: " + json.dumps(payload, ensure_ascii=False))


# ═════════════════════ CLI ═════════════════════
def _arg(args, flag, default=None):
    if flag in args:
        i = args.index(flag)
        if i + 1 < len(args):
            return args[i + 1]
    return default


def main():
    args = sys.argv[1:]
    only_cls = _arg(args, "--class")
    only_profile = _arg(args, "--profile")
    seed = int(_arg(args, "--seed", "42"))
    runs = int(_arg(args, "--runs", "3"))
    target = int(_arg(args, "--to", "10"))
    json_only = "--json" in args

    classes = [only_cls] if only_cls else ALL_CLASSES
    profiles = [only_profile] if only_profile else PROFILES
    seeds = [seed + i for i in range(runs)]

    validate()
    results = {}

    async def go():
        for p in profiles:
            for c in classes:
                results[f"{c}:{p}"] = await run_combo(c, p, seeds, target)

    asyncio.run(go())
    _print_report(results, classes, profiles, target, json_only)


if __name__ == "__main__":
    main()
