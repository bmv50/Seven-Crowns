# -*- coding: utf-8 -*-
"""
Симулятор CADENCE лута вдоль прокачки 1→60 (этап 6.2).

Отвечает на вопросы регламента беты:
  • сколько КИЛЛОВ до первого ДРОПА экипировки в каждом окне уровней;
  • сколько КИЛЛОВ и МИНУТ до первого АПГРЕЙДА (предмет с level_req выше
    носимого в своём слоте — упрощённая метрика регламента);
  • где «засухи» — окна, где апгрейд реже, чем раз в 2–3 сессии (40–90 мин).

А также — для «ненужный лут → ресурс» и pity-крафта:
  • сколько «туманной пыли» естественно копится за окно (разбор junk-дропа);
  • через сколько минут доступен pity-крафт (порог N пыли) после засухи.

Модель:
  • дроп — РЕАЛЬНЫЙ конвейер: rarity.roll_drop + loop._pool_for(level),
    со смещением 60% к классу убийцы (как в engine/loop.on_mob_death);
  • моб уровня ≈ уровня игрока;
  • прокачка — реальная кривая Character.xp_to_next; опыт за килл ≈
    mob_xp(level) × XP_RATE × DIFF_XP(green) (данные mobs.yaml, без rested/season);
  • апгрейд по level_req (метрика регламента) + вторично по стат-скору (рарность);
  • килл ≈ SEC_PER_KILL секунд (бой + перемещения).

Запуск:  python3 sim_loot_cadence.py            — анализ ДО правок
         python3 sim_loot_cadence.py --pity     — + прогноз доступности pity-крафта
         python3 sim_loot_cadence.py --json      — только JSON
"""
import json
import random
import sys

import yaml

from engine import rarity, equip, content
from engine.content import ITEMS
from engine.character import Character
from engine.loop import _pool_for

SEC_PER_KILL = 25          # секунд на килл с учётом перемещений
SESSION_MIN = 25           # средняя длина сессии (мин)
WINDOWS = [(1, 10), (11, 20), (21, 30), (31, 40), (41, 50), (51, 60)]
CLASSES = ["warrior", "paladin", "rogue", "priest", "mage", "necromancer"]

# «Туманная пыль»: формула количества при разборке (та же, что в engine/salvage.py).
# level_req//4 + бонус за редкость. Пыль имеет ценность ТОЛЬКО в крафте (price≈0),
# поэтому фонтан золота исключён.
RARITY_DUST = {"common": 0, "green": 1, "blue": 3, "purple": 8, "gold": 13, "red": 20}


def dust_for(item_key: str) -> int:
    """Сколько пыли даёт разборка предмета (дублирует salvage.dust_for)."""
    base, rar, _ = rarity.split(item_key)
    meta = ITEMS.get(base)
    if not meta:
        return 1
    return max(1, equip.level_req(meta) // 4 + RARITY_DUST.get(rar, 0))


# ── таблица опыта мобов по уровню (данные mobs.yaml, без боссов-выбросов) ──
def _build_mob_xp():
    m = yaml.safe_load(open("data/mobs.yaml", encoding="utf-8"))
    rows = sorted((v.get("level", 1), v.get("xp", 0)) for v in m.values()
                  if isinstance(v, dict) and not v.get("boss"))
    # выбросы-боссы отсекаем по резкому скачку: берём медиану на уровень
    from collections import defaultdict
    byl = defaultdict(list)
    for lv, xp in rows:
        byl[lv].append(xp)
    table = {}
    for lv, xs in byl.items():
        xs.sort()
        table[lv] = xs[len(xs) // 2]     # медиана
    return table


_MOB_XP = _build_mob_xp()


def mob_xp(level: int) -> int:
    """Опыт представительного (не-босс) моба уровня ≈ level: ближайший ≤ level."""
    for lv in range(level, 0, -1):
        if lv in _MOB_XP:
            return _MOB_XP[lv]
    for lv in range(level, 100):
        if lv in _MOB_XP:
            return _MOB_XP[lv]
    return max(5, level * 8)


def _biased_pool(level: int, cls: str):
    """Пул дропа со смещением 60% к классу убийцы (как в loop.on_mob_death)."""
    pool = _pool_for(level)
    if random.random() < 0.6:
        cp = [k for k in pool if equip.class_can_use(cls, k)]
        if cp:
            return cp
    return pool


def window_of(level: int):
    for lo, hi in WINDOWS:
        if lo <= level <= hi:
            return (lo, hi)
    return WINDOWS[-1]


def run_one(cls: str, seed: int, max_kills: int = 200_000):
    """Один забег 1→60. Возвращает пер-оконную статистику."""
    random.seed(seed)
    ch = Character(uid=1, name="sim", cls=cls, race="human")
    ch.level = 1
    ch.xp = 0
    ch.init_vitals()
    worn_lr = {}                # slot -> level_req носимого
    # накопители по окну
    stat = {w: {"kills": 0, "drops": 0, "equip_upg": 0, "score_upg": 0,
                "weap_upg": 0, "first_weap": None,
                "salvaged": 0, "dust": 0,
                "first_drop": None, "first_upg": None,
                "entry_kill": None} for w in WINDOWS}
    total_kills = 0
    xp_acc = 0.0
    while ch.level < 60 and total_kills < max_kills:
        total_kills += 1
        w = window_of(ch.level)
        s = stat[w]
        if s["entry_kill"] is None:
            s["entry_kill"] = total_kills
        s["kills"] += 1
        # ── дроп экипировки (реальный конвейер) ──
        pool = _biased_pool(ch.level, cls)
        drop = rarity.roll_drop(ch.level, pool, boss=False)
        if drop:
            s["drops"] += 1
            if s["first_drop"] is None:
                s["first_drop"] = s["kills"]
            base, rar, seed_ = rarity.split(drop)
            meta = ITEMS[base]
            can = equip.can_equip(ch, drop)[0]
            kept = False
            if can:
                slot = meta.get("slot")
                if slot == "ring":
                    slot = "ring1"
                lr_new = equip.level_req(meta)
                lr_old = worn_lr.get(slot, 0)
                if lr_new > lr_old:
                    # апгрейд по level_req — метрика регламента
                    worn_lr[slot] = lr_new
                    s["equip_upg"] += 1
                    if s["first_upg"] is None:
                        s["first_upg"] = s["kills"]
                    if slot == "weapon":
                        s["weap_upg"] += 1
                        if s["first_weap"] is None:
                            s["first_weap"] = s["kills"]
                    kept = True
                elif lr_new == lr_old and rarity.META[rar]["mult"] > 1.0:
                    # тот же тир, но выше рарность → стат-апгрейд (вторичная метрика)
                    s["score_upg"] += 1
                    kept = True
            if not kept:
                # ненужный лут → разборка в пыль
                s["salvaged"] += 1
                s["dust"] += dust_for(drop)
        # ── прокачка (реальная кривая) ──
        xp_acc += mob_xp(ch.level) * content.XP_RATE * 1.0
        while ch.level < 60 and xp_acc >= ch.xp_to_next:
            xp_acc -= ch.xp_to_next
            ch.level += 1
            ch.init_vitals()
    return stat


def aggregate(cls: str, runs: int = 120):
    agg = {w: {"kills": 0, "drops": 0.0, "equip_upg": 0.0, "score_upg": 0.0,
               "weap_upg": 0.0, "salvaged": 0.0, "dust": 0.0,
               "first_drop": [], "first_upg": [], "first_weap": [],
               "no_upg_runs": 0, "no_weap_runs": 0} for w in WINDOWS}
    for i in range(runs):
        st = run_one(cls, seed=1000 + i)
        for w in WINDOWS:
            a = agg[w]; s = st[w]
            a["kills"] = s["kills"]      # детерминировано (кривая опыта)
            a["drops"] += s["drops"]
            a["equip_upg"] += s["equip_upg"]
            a["score_upg"] += s["score_upg"]
            a["weap_upg"] += s["weap_upg"]
            a["salvaged"] += s["salvaged"]
            a["dust"] += s["dust"]
            if s["first_drop"]:
                a["first_drop"].append(s["first_drop"])
            if s["first_upg"]:
                a["first_upg"].append(s["first_upg"])
            else:
                a["no_upg_runs"] += 1
            if s["first_weap"]:
                a["first_weap"].append(s["first_weap"])
            else:
                a["no_weap_runs"] += 1
    out = {}
    for w in WINDOWS:
        a = agg[w]
        kills = a["kills"]
        upg = a["equip_upg"] / runs
        wupg = a["weap_upg"] / runs
        fdrop = sum(a["first_drop"]) / len(a["first_drop"]) if a["first_drop"] else None
        fupg = sum(a["first_upg"]) / len(a["first_upg"]) if a["first_upg"] else None
        fweap = sum(a["first_weap"]) / len(a["first_weap"]) if a["first_weap"] else None
        dust = a["dust"] / runs
        salv = a["salvaged"] / runs
        out[f"{w[0]}-{w[1]}"] = {
            "kills_in_window": kills,
            "minutes_in_window": round(kills * SEC_PER_KILL / 60, 1),
            "drops": round(a["drops"] / runs, 1),
            "equip_upgrades": round(upg, 2),
            "score_upgrades": round(a["score_upg"] / runs, 2),
            "kills_per_equip_upgrade": round(kills / upg, 1) if upg > 0.05 else None,
            "min_per_equip_upgrade": round(kills / upg * SEC_PER_KILL / 60, 1) if upg > 0.05 else None,
            "first_drop_kills": round(fdrop, 1) if fdrop else None,
            "first_equip_upgrade_kills": round(fupg, 1) if fupg else None,
            "first_equip_upgrade_min": round(fupg * SEC_PER_KILL / 60, 1) if fupg else None,
            "weapon_upgrades": round(wupg, 2),
            "min_per_weapon_upgrade": round(kills / wupg * SEC_PER_KILL / 60, 1) if wupg > 0.05 else None,
            "first_weapon_upgrade_min": round(fweap * SEC_PER_KILL / 60, 1) if fweap else None,
            "runs_without_weapon_pct": round(a["no_weap_runs"] / runs * 100),
            "runs_without_upgrade_pct": round(a["no_upg_runs"] / runs * 100),
            "salvaged": round(salv, 1),
            "dust_total": round(dust, 1),
            "dust_per_salvage": round(dust / salv, 2) if salv > 0 else 0,
        }
    return out


def verdict(minutes):
    if minutes is None:
        return "🏜 ЗАСУХА (апгрейда нет)"
    if minutes <= 30:
        return "✅ отлично (≤1 сессии)"
    if minutes <= 90:
        return "🟢 норма (2–3 сессии)"
    if minutes <= 180:
        return "🟡 редко (>3 сессий)"
    return "🏜 ЗАСУХА (>3 сессий)"


def print_table(cls, data):
    print("═" * 92)
    print(f"  CADENCE ЛУТА — класс {cls} (килл ≈ {SEC_PER_KILL}с, сессия ≈ {SESSION_MIN} мин)")
    print("═" * 92)
    hdr = f"{'окно':>7} | {'киллы':>6} {'мин':>5} | {'дропы':>5} {'до1го':>6} | " \
          f"{'апгр':>5} {'кил/ап':>7} {'мин/ап':>7} | {'вердикт'}"
    print(hdr)
    print("-" * 92)
    for w, d in data.items():
        print(f"{w:>7} | {d['kills_in_window']:>6} {d['minutes_in_window']:>5} | "
              f"{d['drops']:>5} {str(d['first_drop_kills']):>6} | "
              f"{d['equip_upgrades']:>5} {str(d['kills_per_equip_upgrade']):>7} "
              f"{str(d['min_per_equip_upgrade']):>7} | {verdict(d['min_per_equip_upgrade'])}")
    print("-" * 92)
    print("  🗡 ОРУЖИЕ (самый значимый апгрейд; тиры только на ур.1/15/30/45/60):")
    for w, d in data.items():
        wm = d["min_per_weapon_upgrade"]
        note = verdict(wm)
        print(f"{w:>7} | апгр-оружия {d['weapon_upgrades']:>4} | 1й через "
              f"{str(d['first_weapon_upgrade_min']):>5} мин | без оруж-апгр {d['runs_without_weapon_pct']:>3}% забегов | {note}")
    print("-" * 92)
    print("  Пыль/разборка по окну:")
    for w, d in data.items():
        print(f"{w:>7} | разобрано {d['salvaged']:>5} шт | пыль {d['dust_total']:>6} "
              f"(≈{d['dust_per_salvage']}/шт) | стат-апгрейдов рарностью {d['score_upgrades']}")


def suggest_pity(data):
    """Порог N пыли на pity-рецепт = пыль с ~20 разобранных предметов окна."""
    print("\n" + "═" * 92)
    print("  ПРЕДЛОЖЕНИЕ ПОРОГОВ PITY (N пыли ≈ разбор 20 предметов окна = «после серии неудач»)")
    print("═" * 92)
    res = {}
    for w, d in data.items():
        dps = d["dust_per_salvage"] or 1
        n = int(round(dps * 20))
        # сколько киллов до накопления N пыли естественным разбором
        dust_rate = d["dust_total"] / max(1, d["kills_in_window"])   # пыль/килл
        kills_to_pity = int(round(n / max(0.01, dust_rate)))
        res[w] = {"pity_dust_N": n, "kills_to_pity": kills_to_pity,
                  "min_to_pity": round(kills_to_pity * SEC_PER_KILL / 60, 1),
                  "sessions_to_pity": round(kills_to_pity * SEC_PER_KILL / 60 / SESSION_MIN, 1)}
        print(f"{w:>7} | N={n:>4} пыли | ~{kills_to_pity:>4} киллов = "
              f"{res[w]['min_to_pity']:>5} мин = {res[w]['sessions_to_pity']} сессий до pity")
    return res


RECIPE_FOR_WINDOW = {"1-10": "condense_w10", "11-20": "condense_w20",
                     "21-30": "condense_w30", "31-40": "condense_w40",
                     "41-50": "condense_w50", "51-60": "condense_w60"}


def verify_pity(data):
    """ПОВТОРНЫЙ ЗАМЕР: берём реальные N пыли из data/recipes.yaml и считаем,
    через сколько минут/сессий естественного разбора доступен pity-крафт."""
    from engine.content import RECIPES, ITEMS
    print("\n" + "═" * 92)
    print("  ПОВТОРНЫЙ ЗАМЕР — доступность pity по РЕАЛЬНЫМ рецептам (recipes.yaml)")
    print("═" * 92)
    for w, d in data.items():
        rid = RECIPE_FOR_WINDOW[w]
        rec = RECIPES.get(rid)
        if not rec:
            print(f"{w:>7} | рецепт {rid} НЕ НАЙДЕН")
            continue
        n = dict((k, q) for k, q in rec["inputs"]).get("туманная_пыль", 0)
        out = rec["output"]
        dust_rate = d["dust_total"] / max(1, d["kills_in_window"])   # пыль/килл
        kills = int(round(n / max(0.01, dust_rate)))
        mins = kills * SEC_PER_KILL / 60
        sess = mins / SESSION_MIN
        mark = "✅" if sess <= 3 else ("🟡" if sess <= 4 else "❌")
        print(f"{w:>7} | N={n:>4} → {ITEMS[out]['name']:<26} | ~{kills:>4} киллов = "
              f"{mins:>5.1f} мин = {sess:.1f} сессий {mark}")


def main():
    runs = 40 if "--fast" in sys.argv else 120
    only_json = "--json" in sys.argv
    all_data = {}
    for cls in (CLASSES if "--allclasses" in sys.argv else ["warrior"]):
        data = aggregate(cls, runs=runs)
        all_data[cls] = data
        if not only_json:
            print_table(cls, data)
            pity = suggest_pity(data)
            all_data[cls + "_pity"] = pity
            if "--verify" in sys.argv or "--pity" in sys.argv:
                verify_pity(data)
    print("\n===JSON===")
    print(json.dumps(all_data, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
