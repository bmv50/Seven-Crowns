# -*- coding: utf-8 -*-
"""
Боевая логика: формулы урона, применение умений (вкл. AoE), эффекты.
Чистые функции без Telegram. Реал-тайм тики мобов — в loop.py.
"""
import random
from typing import List, Optional, Tuple

from .content import MOBS, SKILLS, ITEMS, HP_SCALE, MOB_ATK_SCALE
from .character import Character, RAGE_ON_ATTACK, RAGE_ON_HIT
from .world import World, MobInstance
from . import bestiary
from . import rules2
from . import weekly


_WEAPON_DTYPE_KW = [
    ("pierce", ["кинжал", "нож", "копь", "рапир", "стилет", "пика", "игла", "коготь"]),
    ("bash",   ["булав", "молот", "дубин", "посох", "палиц", "кистен", "цеп"]),
    ("slash",  ["меч", "топор", "сабл", "клинок", "секира", "коса", "серп"]),
]


def _weapon_dtype(ch) -> str:
    w = ch.equipment.get("weapon")
    if w and w in ITEMS:
        it = ITEMS[w]
        if it.get("dmg_type"):
            return it["dmg_type"]
        low = it.get("name", "").lower()
        for dt, kws in _WEAPON_DTYPE_KW:
            if any(k in low for k in kws):
                return dt
        return "slash"
    return "bash"


def _skill_dtype(sk) -> str:
    if sk.get("dmg_type"):
        return sk["dmg_type"]
    t = (sk.get("effect") or {}).get("type")
    return {"burn": "fire", "freeze": "cold", "bleed": "pierce",
            "poison": "poison"}.get(t, "energy")


def _mark_exploit(mob: MobInstance, uid: int, dtype: str) -> None:
    """Отметить, что игрок uid попал по мобу типом урона из его vuln (rules2) —
    недельная цель dtype_kill (Этап 6.1). Проверка независима от rules2.ENABLED:
    сам митигейт урона включается флагом, а вот факт «удар по слабости» — нет,
    это чисто отметка для weekly, не влияющая на баланс."""
    if not dtype:
        return
    if dtype in rules2.mob_profile(mob.meta)["vuln"]:
        mob.exploited_by.add(uid)

# множитель угрозы по классу: танки удерживают мобов на себе
THREAT_MULT = {"warrior": 2.5, "paladin": 2.2, "priest": 1.4, "necromancer": 1.2}


def threat_mult(ch: Character) -> float:
    return THREAT_MULT.get(ch.cls, 1.0)


# статусы: DoT (poison/burn/bleed) и контроль (freeze/stun)
DOT_TYPES = ("poison", "burn", "bleed")
DISABLE_TYPES = ("freeze", "stun")
STATUS_TYPES = DOT_TYPES + DISABLE_TYPES
STATUS_LABEL = {"poison": "☠️ яд", "burn": "🔥 горение", "bleed": "🩸 кровотечение",
                "freeze": "❄️ заморозка", "stun": "💫 оглушение"}
STATUS_APPLY = {"poison": "☠️ отравлен!", "burn": "🔥 горит!", "bleed": "🩸 истекает кровью!",
                "freeze": "❄️ заморожен!", "stun": "💫 оглушён!"}


def mob_is_disabled(mob) -> bool:
    return any(e.get("type") in DISABLE_TYPES and e.get("turns", 0) > 0 for e in mob.effects)


def mob_is_frozen(mob) -> bool:
    return any(e.get("type") == "freeze" and e.get("turns", 0) > 0 for e in mob.effects)


def apply_status(mob, eff: dict) -> str:
    """Наложить статус на моба. Возвращает строку-уведомление или ''."""
    t = eff.get("type")
    if t not in STATUS_TYPES:
        return ""
    if rules2.ENABLED and t in DISABLE_TYPES and rules2.saves(mob, t):
        return f"   {mob.meta['name']} устоял против контроля!"
    mob.effects.append(dict(eff, turns=eff.get("duration", eff.get("turns", 2))))
    return f"   {mob.meta['name']} {STATUS_APPLY.get(t, 'поражён эффектом!')}"


def player_hit_chance(ch: Character, mob: MobInstance) -> float:
    """Шанс игрока попасть по мобу: база + Ловкость − уклонение моба (от его уровня)."""
    base = 0.90 + ch.attr("dex") * 0.003
    lvl_gap = max(0, mob.meta.get("level", 1) - ch.level)
    evasion = 0.05 + lvl_gap * 0.03
    return min(0.99, max(0.45, base - evasion))


def _roll(base: int, spread: float = 0.2) -> int:
    lo = int(base * (1 - spread))
    hi = int(base * (1 + spread)) + 1
    return max(1, random.randint(lo, max(lo + 1, hi)))


# Градация удара по доле урона от макс. HP цели.
_HIT_TIERS = [
    (0.03, "царапает",   "царапаете"),
    (0.07, "задевает",   "задеваете"),
    (0.13, "бьёт",       "бьёте"),
    (0.22, "рассекает",  "рассекаете"),
    (0.35, "кромсает",   "кромсаете"),
    (0.55, "разрывает",  "разрываете"),
]
_HIT_TOP = ("сокрушает", "сокрушаете")


def hit_verb(dmg: int, target_max_hp: int, you: bool = False) -> str:
    ratio = dmg / max(1, target_max_hp)
    for thr, third, second in _HIT_TIERS:
        if ratio <= thr:
            return second if you else third
    return _HIT_TOP[1] if you else _HIT_TOP[0]


def player_evasion(ch: Character) -> float:
    """Шанс игрока уклониться от удара: база + Ловкость + пассивки/раса."""
    return min(0.45, 0.03 + ch.dodge_chance + ch.attr("dex") * 0.004)


# Щит новичка: входящий урон по персонажам ранних уровней умножается на множитель
# (смягчает раннюю смертность, спасает retention первой сессии). Дальше — 1.0.
#
# Балансировка Этапа 4.1 (рычаг «б»): щит сделан КЛАСС-ЗАВИСИМЫМ. Крепкие классы
# (воин/разбойник/жрец… — те, кто в naive-профиле уже проходит онбординг ≤6 смертей)
# сохраняют исторический щит: окно 1–4 ур., множитель 0.7. Хрупкие классы
# (маг/некромант/жрец/паладин: нулевой damage_reduction ИЛИ низкий hp_base, а в
# naive они без лечения не вывозят базовой атакой) получают УСИЛЕННЫЙ щит — окно
# продлено до 6 ур. и урон режется сильнее (×0.55). Основание — распределение
# смертей naive: пик приходится ровно на 5–7 ур., сразу за прежней границей 4 ур.
# (см. docs/BALANCE_ONBOARDING.md). Окно НЕ выходит за 6 ур. (потолок регламента).
NEWBIE_SHIELD_MAX_LEVEL = 4        # базовое окно (крепкие классы): 1–4 ур.
NEWBIE_SHIELD_MULT = 0.7           # базовый множитель урона под щитом
NEWBIE_SHIELD_FRAGILE = frozenset({"mage", "necromancer", "priest", "paladin"})
NEWBIE_SHIELD_FRAGILE_MAX_LEVEL = 6    # усиленное окно для хрупких: 1–6 ур.
NEWBIE_SHIELD_FRAGILE_MULT = 0.55      # усиленный множитель для хрупких


def newbie_shield_max_level(ch: Character) -> int:
    """До какого уровня (включительно) действует щит новичка у класса персонажа."""
    if ch.cls in NEWBIE_SHIELD_FRAGILE:
        return NEWBIE_SHIELD_FRAGILE_MAX_LEVEL
    return NEWBIE_SHIELD_MAX_LEVEL


def newbie_shield_mult(ch: Character) -> float:
    """Множитель урона под щитом новичка (хрупким классам — ниже, сильнее защита)."""
    if ch.cls in NEWBIE_SHIELD_FRAGILE:
        return NEWBIE_SHIELD_FRAGILE_MULT
    return NEWBIE_SHIELD_MULT


def newbie_shield_factor(ch: Character) -> float:
    """Множитель урона по новичку в его окне щита, иначе 1.0. Отдельно — чтобы
    тестировать. Окно и сила щита класс-зависимы (см. блок констант выше)."""
    return newbie_shield_mult(ch) if ch.level <= newbie_shield_max_level(ch) else 1.0


def apply_damage_to_char(ch: Character, raw: int, dtype: str = "bash",
                         attacker=None) -> Tuple[int, bool]:
    """Урон по персонажу с учётом уклонения/защиты/снижения/щита. -> (итог, увернулся).
    Второй элемент кортежа — True при уклонении. Щит новичка (≤4 ур.) применяется
    ПОСЛЕ всех прочих модификаторов и лишь уменьшает урон, помечая строку иконкой 🛡."""
    for eff in ch.effects:
        if eff.get("type") == "dodge" and random.random() < eff["chance"]:
            return 0, True
    if random.random() < player_evasion(ch):
        return 0, True
    dmg = max(1, raw - ch.defense)
    dmg = int(dmg * (1 - ch.damage_reduction))
    if rules2.ENABLED:
        dmg = rules2.mitigate(dmg, dtype, ch)
        if attacker is not None:
            dmg = int(dmg * rules2.protection_factor(attacker, ch))
    for eff in ch.effects:
        if eff.get("type") == "shield" and eff.get("amount", 0) > 0:
            absorbed = min(eff["amount"], dmg)
            eff["amount"] -= absorbed
            dmg -= absorbed
    # щит новичка — самым последним, поверх всех модификаторов
    _shield = newbie_shield_factor(ch)
    if _shield < 1.0:
        dmg = int(dmg * _shield)
    dmg = max(0, dmg)
    ch.hp -= dmg
    if dmg > 0:
        ch.wear("armor")
    ch.gain_rage(RAGE_ON_HIT)          # воин копит ярость от полученного урона
    return dmg, False


def player_basic_attack(ch: Character, mob: MobInstance) -> List[str]:
    out = []
    hits = 1
    if random.random() < ch.double_strike:
        hits = 2
        out.append("⚡ Двойной удар!")
    if rules2.ENABLED:
        hits = max(hits, rules2.num_attacks(ch))
    # Балансировка мультиатаки (спринт 6): при hits>1 каждый отдельный удар
    # серии ослаблен множителем rules2.multiattack_scale(hits), чтобы суммарный
    # урон серии рос УМЕРЕННО (+12% за 2-ю атаку, +24% за 3-ю, ...), а не кратно
    # числу атак. При hits==1 множитель == 1.0 — поведение не меняется.
    # Применяется и при rules2.ENABLED=False, если double_strike дал hits=2 —
    # это старая механика двойного удара, её мы намеренно НЕ трогаем: масштаб
    # затрагивает только серию, рождённую rules2.num_attacks (флаг ENABLED).
    atk_scale = rules2.multiattack_scale(hits) if rules2.ENABLED else 1.0
    total_dealt = 0
    hit_ch = player_hit_chance(ch, mob)
    shatter = mob_is_frozen(mob)        # комбо: первый удар по льду — гарант. крит
    for _ in range(hits):
        if random.random() > hit_ch:
            out.append(f"🌫 Вы промахиваетесь по {mob.meta['name']}.")
            continue
        dmg = _roll(ch.attack_power)
        crit = random.random() < ch.crit_chance or shatter
        if crit:
            dmg = int(dmg * 1.8)
        dmg = max(1, dmg - mob.meta.get("defense", 0))
        dmg = int(dmg * (1 + bestiary.bonus(ch, mob.mob_id)))
        if atk_scale != 1.0:
            dmg = max(1, int(dmg * atk_scale))
        _wdt = _weapon_dtype(ch)
        if rules2.ENABLED:
            dmg = rules2.mitigate(dmg, _wdt, mob)
        _mark_exploit(mob, ch.uid, _wdt)
        mob.hp -= dmg
        total_dealt += dmg
        mob.add_threat(ch.uid, dmg * threat_mult(ch))
        ch.gain_rage(RAGE_ON_ATTACK)       # воин копит ярость от ударов
        verb = hit_verb(dmg, mob.max_hp, you=True)
        out.append(f"🗡 Вы {verb} {mob.meta['name']} на {dmg}{' 💥КРИТ!' if crit else ''}.")
        if shatter:
            out.append(f"❄️💥 Раскол! Заморозка усиливает удар.")
            mob.effects = [e for e in mob.effects if e.get("type") != "freeze"]
            shatter = False
        if mob.hp <= 0:
            break
    if ch.lifesteal > 0 and total_dealt > 0:
        healed = int(total_dealt * ch.lifesteal)
        if healed > 0 and ch.hp < ch.max_hp:
            ch.hp = min(ch.max_hp, ch.hp + healed)
            out.append(f"🩸 Вы впитываете {healed} HP.")
    if total_dealt > 0:
        ch.wear("weapon")
    return out


def use_skill(ch: Character, skill_id: str, world: World,
              party: List[Character]) -> Tuple[bool, List[str]]:
    if skill_id not in ch.skills:
        return False, ["🚫 Вы не владеете этим умением."]
    sk = SKILLS[skill_id]
    if ch.cooldowns.get(skill_id, 0) > 0:
        return False, [f"⏳ {sk['name']} перезаряжается ({ch.cooldowns[skill_id]})."]
    cost = sk["mp"]
    if ch.mp < cost:
        return False, [f"{ch.resource_emoji} Не хватает: {ch.resource_name} {ch.mp}/{cost}."]

    ch.mp -= cost
    ch.cooldowns[skill_id] = sk["cooldown"]
    out = [f"{sk['emoji']} Вы применяете *{sk['name']}*!"]
    prim = ch.attr(ch.primary_attr)

    if sk["kind"] == "damage":
        # AoE — по всем мобам в комнате; иначе одиночная цель
        if sk.get("aoe"):
            targets = world.living_in(ch.room)
        else:
            mob = world.find(ch.room, ch.target) if ch.target else None
            if not mob:
                mobs = world.living_in(ch.room)
                mob = mobs[0] if mobs else None
            targets = [mob] if mob else []
        if not targets:
            ch.mp += sk["mp"]; ch.cooldowns[skill_id] = 0
            return False, ["🤷 Нет цели для атаки."]
        if not sk.get("aoe"):
            ch.target = targets[0].key
        else:
            out.append("   🌀 Удар по площади!")
        total_dealt = 0
        for mob in targets:
            if ch.uid not in mob.aggro:
                mob.aggro.append(ch.uid)
            if random.random() > player_hit_chance(ch, mob):
                out.append(f"   🌫 Мимо — {mob.meta['name']} уворачивается.")
                continue
            dmg = _roll(int(prim * sk["scaling"]))
            crit = random.random() < (ch.crit_chance + sk.get("crit_bonus", 0.0))
            if crit:
                dmg = int(dmg * 1.8)
            dmg = max(1, dmg - mob.meta.get("defense", 0))
            dmg = int(dmg * (1 + bestiary.bonus(ch, mob.mob_id)))
            _sdt = _skill_dtype(sk)
            if rules2.ENABLED:
                dmg = rules2.mitigate(dmg, _sdt, mob)
            _mark_exploit(mob, ch.uid, _sdt)
            mob.hp -= dmg
            total_dealt += dmg
            mob.add_threat(ch.uid, dmg * threat_mult(ch))
            verb = hit_verb(dmg, mob.max_hp, you=True)
            out.append(f"   Вы {verb} {mob.meta['name']} на {dmg}{' 💥КРИТ!' if crit else ''}.")
            if "effect" in sk and sk["effect"].get("type") in STATUS_TYPES:
                line = apply_status(mob, sk["effect"])
                if line:
                    out.append(line)
        ls = sk.get("lifesteal", 0.0) + ch.lifesteal
        if ls > 0 and total_dealt > 0:
            healed = int(total_dealt * min(1.0, ls))
            if healed > 0 and ch.hp < ch.max_hp:
                ch.hp = min(ch.max_hp, ch.hp + healed)
                out.append(f"   🩸 Вы впитываете {healed} HP.")

    elif sk["kind"] == "heal":
        amount = _roll(int(prim * sk["scaling"])) * HP_SCALE
        if sk["target"] == "allies":
            for ally in party:
                before = ally.hp
                ally.hp = min(ally.max_hp, ally.hp + amount)
                healed = ally.hp - before
                out.append(f"   💚 {ally.name}: +{healed} HP.")
                # недельная цель heal_ally: лечим именно СОЮЗНИКА, не себя (Этап 6.1)
                if healed > 0 and ally.uid != ch.uid:
                    _wl = weekly.on_heal_ally(ch)
                    if _wl:
                        out.append("   " + _wl)
        else:
            before = ch.hp
            ch.hp = min(ch.max_hp, ch.hp + amount)
            out.append(f"   💚 Вы исцелены на {ch.hp - before} HP.")

    elif sk["kind"] == "buff":
        eff = dict(sk["effect"])
        if "amount_scaling" in eff:
            eff["amount"] = int(prim * eff.pop("amount_scaling"))
        eff["turns"] = eff.get("duration", 3)
        if sk.get("target") == "allies":
            for ally in party:
                ally.effects.append(dict(eff))
            out.append(f"   🛡 Эффект на всех союзников ({eff['turns']} ходов).")
        else:
            ch.effects.append(eff)
            out.append(f"   🛡 Эффект наложен на {eff['turns']} ходов.")
        # танки защитной стойкой стягивают угрозу на себя (провокация)
        if threat_mult(ch) >= 2.0:
            burst = ch.max_hp * 0.2
            pulled = False
            for mob in world.living_in(ch.room):
                if mob.aggro:
                    mob.add_threat(ch.uid, burst)
                    pulled = True
            if pulled:
                out.append("   🎯 Вы провоцируете врагов — их внимание на вас!")

    return True, out


def tick_effects_char(ch: Character) -> List[str]:
    out = []
    survived = []
    for eff in ch.effects:
        if "turns" in eff:
            eff["turns"] -= 1
        if eff.get("type") == "shield" and eff.get("amount", 1) <= 0:
            continue
        if eff.get("turns", 1) > 0:
            survived.append(eff)
    ch.effects = survived
    for sk in list(ch.cooldowns):
        if ch.cooldowns[sk] > 0:
            ch.cooldowns[sk] -= 1
    return out


def advance_player_turn(ch: Character) -> None:
    """Продвинуть «ход» игрока: тик кулдаунов/баффов и реген ресурса.
    Вызывается на каждом боевом действии игрока."""
    tick_effects_char(ch)
    ch.regen_resource()


def tick_effects_mob(mob: MobInstance) -> List[str]:
    out = []
    survived = []
    for eff in mob.effects:
        t = eff.get("type")
        if t in DOT_TYPES:
            pdmg = eff.get("dmg", eff.get("amount", 1)) * HP_SCALE
            mob.hp -= pdmg
            out.append(f"{STATUS_LABEL.get(t, '☠️')}: {mob.meta['name']} теряет {pdmg} HP.")
        eff["turns"] = eff.get("turns", 1) - 1
        if eff["turns"] > 0:
            survived.append(eff)
    mob.effects = survived
    return out


def mob_attack(mob: MobInstance, ch: Character) -> List[str]:
    out = []
    m = mob.meta
    skills = m.get("skills", [])
    if skills and random.random() < 0.25:
        sk = SKILLS[random.choice(skills)]
        if sk["kind"] == "damage":
            # множители скиллов рассчитаны под игрока; для мобов ограничиваем
            mob_scaling = min(sk.get("scaling", 1.5), 2.0)
            raw = _roll(int(m["atk"] * mob_scaling * MOB_ATK_SCALE))
            dmg, dodged = apply_damage_to_char(ch, raw, rules2.mob_attack_dtype(m), mob)
            if dodged:
                out.append(f"💨 {ch.name} уклоняется от {sk['name']}!")
            else:
                verb = hit_verb(dmg, ch.max_hp)
                out.append(f"{sk['emoji']} {m['name']} {verb} {ch.name} ({sk['name']}) на {dmg}."
                           + _shield_note(ch))
            return out
    raw = _roll(int(m["atk"] * MOB_ATK_SCALE))
    dmg, dodged = apply_damage_to_char(ch, raw, rules2.mob_attack_dtype(m), mob)
    if dodged:
        out.append(f"💨 {ch.name} уклоняется от удара {m['name']}.")
    else:
        verb = hit_verb(dmg, ch.max_hp)
        out.append(f"💢 {m['name']} {verb} {ch.name} на {dmg}." + _shield_note(ch))
    return out


def _shield_note(ch: Character) -> str:
    """Приписка про щит новичка к строке урона, если он активен (≤4 ур.)."""
    return " 🛡 защита новичка" if newbie_shield_factor(ch) < 1.0 else ""


# ───────── PvP: дуэли между игроками ─────────
def player_vs_player(attacker: Character, defender: Character) -> List[str]:
    """Один удар игрока по игроку (дуэль). Учитывает атаку/защиту/крит/уклонение.
    HP защитника не уходит ниже 1 — дуэль до падения, не до смерти."""
    out = []
    # шанс уклонения защитника
    if random.random() < player_evasion(defender):
        out.append(f"🌫 {defender.name} уклоняется от удара {attacker.name}.")
        return out
    dmg = _roll(attacker.attack_power)
    crit = random.random() < attacker.crit_chance
    if crit:
        dmg = int(dmg * 1.8)
    dmg = max(1, dmg - defender.defense)
    dmg = int(dmg * (1 - defender.damage_reduction))
    # щиты защитника
    for eff in defender.effects:
        if eff.get("type") == "shield" and eff.get("amount", 0) > 0:
            absorbed = min(eff["amount"], dmg)
            eff["amount"] -= absorbed
            dmg -= absorbed
    dmg = max(0, dmg)
    # не опускаем ниже 1 HP
    dmg = min(dmg, max(0, defender.hp - 1))
    defender.hp -= dmg
    attacker.gain_rage(RAGE_ON_ATTACK)
    defender.gain_rage(RAGE_ON_HIT)
    verb = hit_verb(dmg, defender.max_hp, you=True)
    out.append(f"⚔️ {attacker.name} {verb} {defender.name} на {dmg}{' 💥КРИТ!' if crit else ''}.")
    if defender.hp <= 1:
        out.append(f"🏳️ {defender.name} повержен!")
    return out


# ───────── оценка сложности моба относительно игрока ─────────
def mob_difficulty(player_level: int, mob_level: int) -> str:
    """green — лёгкий, yellow — средний, red — опасный (для текущего уровня)."""
    diff = mob_level - player_level
    if diff <= 0:
        return "green"
    if diff <= 3:
        return "yellow"
    return "red"


# множители награды за вызов: жёлтые/красные дают больше денег и лучше лут
DIFF_GOLD = {"green": 1.0, "yellow": 1.6, "red": 2.4}
DIFF_LOOT = {"green": 1.0, "yellow": 1.35, "red": 1.8}
DIFF_XP = {"green": 1.0, "yellow": 1.3, "red": 1.7}
