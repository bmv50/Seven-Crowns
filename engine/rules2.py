# -*- coding: utf-8 -*-
"""
Параллельное ядро правил (гибрид классического ROM/Diku и текущей боёвки).

Живёт РЯДОМ со старым кодом и НЕ меняет поведение, пока ENABLED=False.
Старая боёвка (combat.py) при выключенном флаге работает как раньше; при
включённом — combat.py вызывает хелперы отсюда (типы урона, резисты,
спасброски, мультиатака, мировоззрение).

Профили (резисты/иммунитеты/уязвимости/мировоззрение) читаются ОПЦИОНАЛЬНО
из данных: расы (RACES[...]), мобов (MOBS[...]), персонажа (ch.flags).
Если поля нет — эффекта нет, поэтому миграция контента постепенная.
"""
from .content import RACES, MOBS

# ── глобальный переключатель ядра ──
ENABLED = False     # включается из конфигурации/бота; по умолчанию старое поведение

# ── типы урона ──
DAMAGE_TYPES = [
    "bash", "pierce", "slash",            # физический
    "fire", "cold", "lightning", "acid",  # стихии
    "poison", "disease", "negative",      # яд/болезнь/тьма
    "holy", "energy", "mental", "light",  # свет/энергия/разум
]
PHYSICAL = {"bash", "pierce", "slash"}

# модификаторы митигейта
RESIST_MULT = 0.67   # -33%
VULN_MULT = 1.5      # +50%

# ── мировоззрение ──
ALIGN_MIN, ALIGN_MAX = -1000, 1000
ALIGN_GOOD = 350
ALIGN_EVIL = -350


# ───────── профили защиты ─────────
def _is_char(e) -> bool:
    return hasattr(e, "race") and hasattr(e, "flags")


def _is_mob(e) -> bool:
    return hasattr(e, "mob_id")


def defense_sets(entity):
    """Вернуть (resist, immune, vuln) как множества типов урона для сущности."""
    if _is_char(entity):
        rp = _race_profile(entity.race)
        extra_res = {e.get("resist") for e in entity.effects if e.get("type") == "resist"}
        extra_res.discard(None)
        return (rp["resist"] | extra_res, rp["immune"], rp["vuln"])
    if _is_mob(entity):
        p = mob_profile(entity.meta)
        return (p["resist"], p["immune"], p["vuln"])
    return (set(), set(), set())


def mitigate(amount: int, dtype: str, defender) -> int:
    """Применить резист/иммунитет/уязвимость к урону данного типа."""
    if amount <= 0 or not dtype:
        return max(0, amount)
    resist, immune, vuln = defense_sets(defender)
    if dtype in immune:
        return 0
    if dtype in vuln:
        amount = int(amount * VULN_MULT)
    elif dtype in resist:
        amount = int(amount * RESIST_MULT)
    return max(0, amount)


# ───────── мировоззрение ─────────
def alignment(entity) -> int:
    if _is_char(entity):
        if "alignment" in entity.flags:
            return int(entity.flags["alignment"])
        return _race_profile(entity.race)["align"]
    if _is_mob(entity):
        return mob_profile(entity.meta)["alignment"]
    return 0


def align_label(val: int) -> str:
    if val >= ALIGN_GOOD:
        return "good"
    if val <= ALIGN_EVIL:
        return "evil"
    return "neutral"


def protection_factor(attacker, defender) -> float:
    """Защита от зла/добра: -25% урона, если защитник под protection и
    атакующий противоположного мировоззрения."""
    prot = None
    if _is_char(defender):
        for e in defender.effects:
            if e.get("type") == "protection":
                prot = e.get("vs")
    if not prot:
        return 1.0
    al = align_label(alignment(attacker))
    if (prot == "evil" and al == "evil") or (prot == "good" and al == "good"):
        return 0.75
    return 1.0


# ───────── спасброски от контроля ─────────
# kind: sleep/charm/stun/blind/fear/poison... — чем выше уровень и
# профильный стат, тем выше шанс устоять.
def save_chance(defender, kind: str = "control") -> float:
    lvl = getattr(defender, "level", None)
    if lvl is None:
        lvl = defender.meta.get("level", 1) if _is_mob(defender) else 1
    base = 0.05 + lvl * 0.005
    if _is_char(defender):
        # телосложение/дух помогают против контроля
        try:
            base += defender.attr("spi") * 0.01 + defender.attr("str") * 0.003
        except Exception:
            pass
    # иммунитеты мобов из данных (например NO_SLEEP)
    if _is_mob(defender):
        immune = defender.meta.get("save_immune", [])
        if kind in immune:
            return 1.0
    return max(0.05, min(0.85, base))


def saves(defender, kind: str = "control") -> bool:
    """True — защитник устоял (эффект НЕ накладывается)."""
    import random
    return random.random() < save_chance(defender, kind)


# ───────── мультиатака ─────────
def num_attacks(attacker) -> int:
    """Сколько ударов наносит атакующий за раунд (база 1)."""
    n = 1
    lvl = getattr(attacker, "level", 1)
    cls = getattr(attacker, "cls", None)
    # вторая атака: воины/разбойники раньше, остальные позже
    second_at = {"warrior": 5, "rogue": 12, "paladin": 10}.get(cls, 30)
    third_at = {"warrior": 12, "rogue": 24}.get(cls, 999)
    if lvl >= second_at:
        n += 1
    if lvl >= third_at:
        n += 1
    # хейст/слоу из эффектов
    if _is_char(attacker):
        types = {e.get("type") for e in attacker.effects}
        if "haste" in types:
            n += 1
        if "slow" in types:
            n = max(1, n - 1)
    return n


# Балансировка мультиатаки (спринт 5→6): num_attacks сам по себе давал ПОЛНЫЙ
# урон за каждую доп. атаку — суммарный DPS рос кратно (n атак = n× урона),
# что дало +60…+200% DPS во всех классах на Monte-Carlo замере (см. sim_rules2.py
# и таблицу в отчёте по флагу RULES_V2). multiattack_scale ослабляет КАЖДЫЙ
# отдельный удар серии так, чтобы суммарный урон n атак рос УМЕРЕННО и линейно
# от n, а не кратно: суммарный множитель = 1 + 0.12×(n−1) (2 атаки → +12%,
# 3 → +24%, 4 (хейст поверх 3-й атаки) → +36%). Ощущение мультиатаки сохраняется
# (несколько строк урона за раунд), а прирост DPS остаётся умеренным.
MULTIATTACK_BONUS_PER_EXTRA = 0.12   # прирост суммарного урона за каждую доп. атаку


def multiattack_scale(n: int) -> float:
    """Множитель урона ОДНОЙ атаки в серии из n атак за раунд.

    scale(n) = (1 + 0.12×(n−1)) / n — так, чтобы n одинаковых по силе ударов
    в сумме давали базовый_урон × (1 + 0.12×(n−1)), а не n×базовый_урон.
    n=1 → 1.0 (без изменений, старое поведение при единственной атаке);
    n=2 → 0.56 (2×0.56=1.12 → +12%); n=3 → ~0.4133 (3×0.4133=1.24 → +24%);
    n=4 → 0.34 (4×0.34=1.36 → +36%, хейст поверх третьей атаки).
    Крит/уклонение/резисты применяются ПОВЕРХ этого множителя, как и раньше."""
    n = max(1, int(n))
    return (1 + MULTIATTACK_BONUS_PER_EXTRA * (n - 1)) / n


# ───────── инференс профилей из имени/уровня (миграция без правки YAML) ─────────
# Явные поля в данных (MOBS/RACES) имеют приоритет; иначе выводим по ключевым словам.
_CAT_KEYWORDS = [
    ("undead",    ["скелет", "призрак", "упырь", "мертвец", "утоплен", "утопш",
                   "плакальщик", "кающ", "зомби", "мумия", "личь", "некро", "костя"]),
    ("demon",     ["тёмный рыцарь", "пепельный рыцарь", "демон", "инфернал",
                   "падший", "осквернённ", "проклят"]),
    ("fire",      ["пепельн", "огнен", "пламен", "жар", "лавов", "магмов"]),
    ("ice",       ["замёрз", "замерз", "ледян", "морозн", "снежн", "иней", "кристальн"]),
    ("spirit",    ["дух", "туманн", "призрачн", "фантом", "буревестник"]),
    ("construct", ["голем", "бур", "истукан", "автоматон"]),
    ("beast",     ["волк", "паук", "лис", "олень", "кабан", "крыса", "мышь",
                   "клещ", "пиявка", "секач", "хищник", "мотылёк", "слизень",
                   "падальщик", "пряха", "клыкаст", "змей", "кьяр", "левиафан"]),
]

_CAT_PROFILE = {
    "undead":    {"dmg_type": "negative", "immune": ["poison", "disease"],
                  "resist": ["cold", "negative"], "vuln": ["holy", "fire"], "align": -400},
    "demon":     {"dmg_type": "negative", "resist": ["fire", "negative"],
                  "vuln": ["holy"], "align": -500},
    "fire":      {"dmg_type": "fire", "immune": ["fire"], "vuln": ["cold"], "align": -200},
    "ice":       {"dmg_type": "cold", "resist": ["cold"], "vuln": ["fire"], "align": 0},
    # Дух: бесплотную туманную форму не разрезать и не проколоть (pierce/slash
    # резистятся), НО дробящее (bash — освящённая булава/посох) проходит
    # полновесно: удар сокрушает форму физической силой воздействия, а не
    # кромкой лезвия. Контрплей игрокам с режущим/колющим оружием — скиллы
    # нефизических типов урона (см. combat._skill_dtype/SKILLS) и подсказки
    # «уязв:» в описании комнаты/моба (holy/energy бьют духов ПОВЫШЕННО).
    "spirit":    {"dmg_type": "energy", "resist": ["pierce", "slash"],
                  "vuln": ["holy", "energy"], "align": -100},
    "construct": {"dmg_type": "bash", "immune": ["poison", "disease", "mental"],
                  "resist": ["pierce"], "vuln": ["bash"], "align": 0},
    "beast":     {"dmg_type": "pierce", "align": 0},
}


def infer_category(name: str) -> str:
    low = (name or "").lower()
    for cat, kws in _CAT_KEYWORDS:
        if any(k in low for k in kws):
            return cat
    return "default"


def mob_profile(meta: dict) -> dict:
    """Полный профиль моба: явные поля YAML поверх инференса по имени."""
    cat = infer_category(meta.get("name", ""))
    base = dict(_CAT_PROFILE.get(cat, {}))
    prof = {
        "dmg_type": meta.get("dmg_type", base.get("dmg_type", "bash")),
        "resist": set(meta.get("resist", base.get("resist", []))),
        "immune": set(meta.get("immune", base.get("immune", []))),
        "vuln": set(meta.get("vuln", base.get("vuln", []))),
        "alignment": int(meta.get("alignment", base.get("align", 0))),
        "category": cat,
    }
    return prof


def mob_attack_dtype(meta: dict) -> str:
    return mob_profile(meta)["dmg_type"]


# профили игровых рас (наши расы; явные поля RACES имеют приоритет)
RACE_PROFILE = {
    "human":  {"resist": [], "vuln": [], "align": 0},
    "elf":    {"resist": ["mental", "charm"], "vuln": [], "align": 250},
    "dwarf":  {"resist": ["poison", "disease"], "vuln": [], "align": 50},
    "orc":    {"resist": ["disease"], "vuln": ["holy"], "align": -250},
    "goblin": {"resist": ["poison"], "vuln": ["holy"], "align": -200},
}


def _race_profile(race: str) -> dict:
    src = RACES.get(race, {})
    base = RACE_PROFILE.get(race, {})
    return {
        "resist": set(src.get("resist", base.get("resist", []))),
        "immune": set(src.get("immune", base.get("immune", []))),
        "vuln": set(src.get("vuln", base.get("vuln", []))),
        "align": int(src.get("alignment", base.get("align", 0))),
    }


def validate_damage_types():
    """Проверить, что все типы урона в данных мобов/умений валидны."""
    bad = []
    for k, m in MOBS.items():
        dt = m.get("dmg_type")
        if dt and dt not in DAMAGE_TYPES:
            bad.append((k, dt))
        for field in ("resist", "immune", "vuln"):
            for t in m.get(field, []):
                if t not in DAMAGE_TYPES:
                    bad.append((k, f"{field}:{t}"))
    return bad
