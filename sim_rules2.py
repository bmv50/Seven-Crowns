# -*- coding: utf-8 -*-
"""
Monte-Carlo замер ΔDPS от гибридного ядра rules2 (мультиатака + резисты/имм./увяз.)
относительно старого поведения (rules2.ENABLED=False), ПОСЛЕ балансировки
мультиатаки (см. rules2.multiattack_scale, спринт 6).

Для каждого из 6 классов на уровнях 15 и 45, экипированных снаряжением
items_gen соответствующего тира, прогоняется Monte-Carlo серия боёв против
манекенов четырёх профилей защиты (undead/demon/spirit/default — те же
профили, что rules2.mob_profile применяет к реальным мобам по инференсу
имени). Манекен не наносит урона игроку (HP игрока держим огромным) —
это чистый DPS-парс, боевая ВЫЖИВАЕМОСТЬ проверяется отдельно в sim_player
и sim_endgame (см. критерии решения по флагу RULES_V2 в отчёте).

DPS считается как суммарный урон игрока за один бой (30 раундов обмена
"атака игрока -> тик эффектов манекена", без учёта входящего урона),
усреднённый по прогонам, для ENABLED=False и ENABLED=True. ΔDPS% — то,
на сколько суммарный урон вырос/упал при включении rules2.

ИТОГ ЗАМЕРА (N=350×30, см. отчёт по флагу RULES_V2 в README/.env.example):
мультиатака сама по себе укладывается в коридор ±25% для всех 6 классов на
ур.15/45 против профилей undead/demon/default (+10.6%..+23.7%). Профиль
spirit (резист сразу ко всем физическим типам урона) выбивает 10 из 48
комбинаций класс×уровень×профиль за коридор (-17%..-42%) — это отдельная
особенность профиля резистов, не связанная с балансировкой мультиатаки.

Запуск:
    python sim_rules2.py                       # полная таблица (6 классов × 2 уровня)
    python sim_rules2.py --runs 500             # другое число прогонов на комбинацию
    python sim_rules2.py --classes warrior,mage # посчитать только эти классы
                                                 # (кэшируется в .sim_rules2_cache.json,
                                                 #  удобно для расчёта по частям)
    python sim_rules2.py --report               # только напечатать таблицу из кэша
"""
import json
import os
import random
import sys

from engine.content import ITEMS
from engine.character import Character
from engine import combat, rules2, equip as _equip

CLASS_LIST = ["warrior", "mage", "rogue", "priest", "paladin", "necromancer"]
LEVELS = (15, 45)
RUNS_DEFAULT = 350       # ≥300, как в задании
ROUNDS_PER_FIGHT = 30
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".sim_rules2_cache.json")

# тиры items_gen.py (TIERS = [1, 15, 30, 45, 60, 75, 90]) — берём тир,
# совпадающий с целевым уровнем игрока (15 и 45 — сами являются тирами).
GEAR_TIERS = [1, 15, 30, 45, 60, 75, 90]


def _tier_for(level: int) -> int:
    """Наибольший тир снаряжения, не превышающий уровень игрока."""
    cand = [t for t in GEAR_TIERS if t <= level]
    return max(cand) if cand else GEAR_TIERS[0]


# ───────── профили манекенов (те же 4, что rules2._CAT_PROFILE) ─────────
# Профиль применяется НАПРЯМУЮ (без инференса по имени) — это гарантирует,
# что каждый класс на каждом уровне бьётся против ОДНОЙ и той же категории
# резистов/иммунитетов/уязвимостей, а не против случайно похожего по имени
# реального моба, которого может не быть на нужном уровне в mobs.yaml.
DUMMY_PROFILES = {
    "undead":  dict(rules2._CAT_PROFILE["undead"]),
    "demon":   dict(rules2._CAT_PROFILE["demon"]),
    "spirit":  dict(rules2._CAT_PROFILE["spirit"]),
    "default": {"dmg_type": "bash", "align": 0},   # без резистов/иммунитетов/уязвимостей
}
CATEGORIES = ["undead", "demon", "spirit", "default"]


class DummyMob:
    """Манекен-цель: не наносит урона (чистый DPS-парс исходящего урона игрока),
    но несёт профиль защиты (резист/иммунитет/уязвимость) нужной категории —
    так же, как это делает rules2.mitigate для настоящего MobInstance."""
    __slots__ = ("meta", "mob_id", "effects", "aggro", "threat", "hp", "max_hp")

    def __init__(self, level: int, category: str):
        prof = DUMMY_PROFILES[category]
        self.meta = {
            "name": f"манекен_{category}", "level": level, "defense": 0,
            "dmg_type": prof.get("dmg_type", "bash"),
            "resist": prof.get("resist", []),
            "immune": prof.get("immune", []),
            "vuln": prof.get("vuln", []),
            "alignment": prof.get("align", 0),
        }
        self.mob_id = f"манекен_{category}"
        self.effects = []
        self.aggro = []
        self.threat = {}
        self.hp = 10 ** 9
        self.max_hp = 10 ** 9

    def add_threat(self, uid, amount):
        if uid not in self.aggro:
            self.aggro.append(uid)
        self.threat[uid] = self.threat.get(uid, 0.0) + max(0.0, amount)


def _pick_weapon(cls: str, tier: int):
    """Ключ оружия items_gen для класса/тира, разрешённого правилами equip."""
    for key in sorted(ITEMS):
        if not key.startswith("g_") or f"_{tier}" != key[key.rfind("_"):]:
            continue
        meta = ITEMS[key]
        if meta.get("type") != "weapon" or meta.get("slot") != "weapon":
            continue
        if _equip.class_can_use(cls, key):
            return key
    return None


def _pick_armor(cls: str, tier: int):
    """Ключ брони (слот armor) items_gen для класса/тира по правилам веса."""
    for key in sorted(ITEMS):
        meta = ITEMS.get(key, {})
        if meta.get("type") != "armor" or meta.get("slot") != "armor":
            continue
        if not key.endswith(f"_{tier}"):
            continue
        if _equip.class_can_use(cls, key):
            return key
    return None


def build_char(cls: str, level: int, uid: int) -> Character:
    """Персонаж нужного класса/уровня, экипированный g_*_{tier} по правилам
    класса (оружие + броня armor-слота), tier = наибольший ≤ уровню."""
    ch = Character(uid=uid, name="ДПС-Тест", cls=cls, race="human", level=level)
    tier = _tier_for(level)
    w = _pick_weapon(cls, tier)
    a = _pick_armor(cls, tier)
    if w:
        ch.equipment["weapon"] = w
        ch.set_durab("weapon", 100)
    if a:
        ch.equipment["armor"] = a
        ch.set_durab("armor", 100)
    ch.init_vitals()
    ch.hp = 10 ** 9          # манекен не бьёт в ответ, но держим огромный HP на всякий случай
    return ch


def _one_fight(ch: Character, dummy: DummyMob, rounds: int = ROUNDS_PER_FIGHT) -> int:
    """Один бой: игрок бьёт манекен ROUNDS_PER_FIGHT раз подряд (базовая атака,
    как в реальном combat.player_basic_attack). Возвращает суммарный урон."""
    dummy.hp = 10 ** 9
    total = 0
    for _ in range(rounds):
        combat.advance_player_turn(ch)
        before = dummy.hp
        combat.player_basic_attack(ch, dummy)
        total += before - dummy.hp
    return total


def dps_for(cls: str, level: int, category: str, runs: int, seed_base: int) -> float:
    """Средний суммарный урон за бой (RUNS прогонов по ROUNDS_PER_FIGHT раундов).
    Персонаж и манекен создаются один раз и переиспользуются между прогонами
    (между боями не остаётся эффектов — манекен не бьёт в ответ) — это на
    порядок дешевле, чем пересоздавать Character на каждый прогон."""
    ch = build_char(cls, level, uid=10_000)
    dummy = DummyMob(level, category)
    total = 0
    for i in range(runs):
        random.seed(seed_base + i)
        total += _one_fight(ch, dummy)
    return total / runs


def compute(cls_subset, runs: int):
    """Посчитать (или досчитать) ΔDPS для указанных классов и слить в кэш на диске."""
    cache = {}
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, encoding="utf-8") as f:
            cache = json.load(f)
    cache.setdefault("runs", runs)
    cache.setdefault("rows", {})

    for cls in cls_subset:
        for level in LEVELS:
            key = f"{cls}:{level}"
            per_cat = {}
            for cat in CATEGORIES:
                seed_base = hash((cls, level, cat)) % 1_000_000
                old = dps_for(cls, level, cat, runs, seed_base)
                rules2.ENABLED = True
                try:
                    new = dps_for(cls, level, cat, runs, seed_base)
                finally:
                    rules2.ENABLED = False
                delta = (new - old) / max(1.0, old) * 100
                per_cat[cat] = {"old": old, "new": new, "delta": delta}
            cache["rows"][key] = per_cat
            print(f"  готово: {cls:<12} ур.{level}", flush=True)

    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)
    return cache


def report(cache):
    runs = cache.get("runs", RUNS_DEFAULT)
    rows = cache.get("rows", {})
    print("═" * 92)
    print(f"  MONTE-CARLO ΔDPS: rules2 (мультиатака+резисты) vs старое ядро")
    print(f"  {runs} боёв × {ROUNDS_PER_FIGHT} раундов на (класс, уровень, профиль моба)")
    print("═" * 92)

    header = f"{'класс':<12}{'ур.':>4}  " + "".join(f"{c:<11}" for c in CATEGORIES) + "  среднее ΔDPS%"
    print(header)
    print("-" * len(header))
    worst = []
    missing = []
    for cls in CLASS_LIST:
        for level in LEVELS:
            key = f"{cls}:{level}"
            if key not in rows:
                missing.append(key)
                continue
            per_cat = rows[key]
            cells = "".join(f"{per_cat[c]['delta']:>+9.1f}%  " for c in CATEGORIES)
            avg_delta = sum(per_cat[c]["delta"] for c in CATEGORIES) / len(CATEGORIES)
            print(f"{cls:<12}{level:>4}  {cells}{avg_delta:>+8.1f}%")
            for c in CATEGORIES:
                worst.append((abs(per_cat[c]["delta"]), cls, level, c, per_cat[c]["delta"]))

    print("-" * len(header))
    if missing:
        print(f"  (не посчитано ещё: {', '.join(missing)})")
    worst.sort(reverse=True)
    print("\nСамые большие отклонения ΔDPS от 0% (топ-8, ориентир — коридор ±25%):")
    for absd, cls, level, cat, delta in worst[:8]:
        flag = "❌ ВНЕ ±25%" if absd > 25 else "✅ в коридоре"
        print(f"  {cls:<12} ур.{level:<3} vs {cat:<9}: {delta:+7.1f}%  {flag}")

    n_out = sum(1 for absd, *_ in worst if absd > 25)
    n_total = len(worst)
    if n_total:
        print(f"\nИтого: {n_total - n_out}/{n_total} комбинаций (класс×уровень×профиль моба) "
              f"в коридоре ±25%; нарушителей: {n_out}.")
    print("═" * 92)
    return worst


def run(runs: int = RUNS_DEFAULT, cls_subset=None):
    cache = compute(cls_subset or CLASS_LIST, runs)
    return report(cache)


if __name__ == "__main__":
    runs = RUNS_DEFAULT
    if "--runs" in sys.argv:
        runs = int(sys.argv[sys.argv.index("--runs") + 1])
    subset = None
    if "--classes" in sys.argv:
        subset = sys.argv[sys.argv.index("--classes") + 1].split(",")
    if "--report" in sys.argv:
        if not os.path.exists(CACHE_PATH):
            print("Кэш пуст — сначала посчитайте: python sim_rules2.py --classes ..."); sys.exit(1)
        with open(CACHE_PATH, encoding="utf-8") as f:
            report(json.load(f))
    else:
        run(runs, subset)
