# -*- coding: utf-8 -*-
"""Модель персонажа и расчёт производных характеристик."""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .content import CLASSES, ITEMS, SKILLS, RACES, HP_SCALE

# ───────── ресурсы классов ─────────
# mana — кастеры (копится медленно), energy — разбойник (быстрый реген),
# rage — воин (копится от ударов и полученного урона, обнуляется вне боя).
RESOURCE_META = {
    "mana":   {"emoji": "💙", "name": "Мана"},
    "rage":   {"emoji": "🔥", "name": "Ярость"},
    "energy": {"emoji": "⚡", "name": "Энергия"},
}
RESOURCE_MAX_FIXED = 100      # для ярости и энергии — фиксированный пул
ENERGY_REGEN = 25             # энергия восстанавливается за ход
RAGE_ON_ATTACK = 12           # ярость за успешный удар
RAGE_ON_HIT = 6               # ярость за полученный удар
LEVEL_CAP = 60            # максимальный уровень (далее — реморт-престиж)
DURAB_MAX = 100           # макс. прочность снаряжения
REPAIR_RATE = 50          # бронза за очко ремонта
WEAR_SLOTS = ("weapon", "armor")


@dataclass
class Character:
    uid: int
    name: str
    cls: str                      # ключ класса
    race: str = "human"           # ключ расы
    room: str = "village"
    level: int = 1
    xp: int = 0
    hp: int = 0
    mp: int = 0
    gold: int = 3000          # деньги в бронзе (30 серебра)
    equipment: Dict[str, Optional[str]] = field(default_factory=lambda: {
        slot: None for slot in (
            "weapon", "shield", "armor", "head", "hands", "legs", "feet",
            "cloak", "belt", "neck", "ring1", "ring2", "wrist", "accessory")})
    inventory: List[str] = field(default_factory=list)
    target: Optional[str] = None          # "room:mob" текущая цель
    cooldowns: Dict[str, int] = field(default_factory=dict)
    effects: List[dict] = field(default_factory=list)
    quests: Dict[str, str] = field(default_factory=dict)
    flags: Dict[str, bool] = field(default_factory=dict)
    # умения: выученные и выбранные в боевую панель (до 5)
    learned: List[str] = field(default_factory=list)
    loadout: List[str] = field(default_factory=list)

    LOADOUT_MAX = 5

    # ───────── раса ─────────
    @property
    def race_data(self) -> dict:
        return RACES.get(self.race, RACES["human"])

    def race_passive(self, name: str, default=0.0):
        return self.race_data.get("passives", {}).get(name, default)

    # ───────── атрибуты ─────────
    def _base_attr(self, attr: str) -> int:
        c = CLASSES[self.cls]
        base = c["base"][attr]
        growth = c["per_level"].get(attr, 0) * (self.level - 1)
        race_mod = self.race_data.get("attr_mod", {}).get(attr, 0)
        return base + growth + race_mod

    def attr(self, name: str) -> int:
        """Итоговый атрибут с учётом экипировки и эффектов."""
        val = self._base_attr(name)
        for slot, item_key in self.equipment.items():
            if item_key and not (slot in WEAR_SLOTS and self.durab(slot) == 0):
                val += ITEMS[item_key].get("bonus", {}).get(name, 0)
        for eff in self.effects:
            if eff.get("type") == "attr" and eff.get("attr") == name:
                val += eff["amount"]
        from . import sockets
        val += sockets.stat_bonus(self, name)
        return max(0, val)

    @property
    def max_hp(self) -> int:
        c = CLASSES[self.cls]
        con = c["base_hp"] + c["per_level"]["hp"] * (self.level - 1)
        total = con + self.attr("str") + self.attr("spi") // 2
        return int(total * self.race_data.get("hp_mod", 1.0) * (1 + self._talent("hp_pct") + self.remort_bonus)) * HP_SCALE

    @property
    def max_mp(self) -> int:
        c = CLASSES[self.cls]
        base = c["base_mp"] + c["per_level"]["mp"] * (self.level - 1)
        total = base + self.attr("int") + self.attr("spi")
        return int(total * self.race_data.get("mp_mod", 1.0))

    @property
    def primary_attr(self) -> str:
        return CLASSES[self.cls]["primary"]

    def _talent(self, stat: str) -> float:
        from . import talents
        return talents.bonus(self, stat)

    # ───────── ресурс класса (мана/ярость/энергия) ─────────
    @property
    def resource_type(self) -> str:
        return CLASSES[self.cls].get("resource", "mana")

    @property
    def max_resource(self) -> int:
        """Макс. текущего ресурса. Мана = max_mp, ярость/энергия = 100."""
        if self.resource_type in ("rage", "energy"):
            return RESOURCE_MAX_FIXED
        return self.max_mp

    @property
    def resource_emoji(self) -> str:
        return RESOURCE_META.get(self.resource_type, RESOURCE_META["mana"])["emoji"]

    @property
    def resource_name(self) -> str:
        return RESOURCE_META.get(self.resource_type, RESOURCE_META["mana"])["name"]

    def start_resource(self) -> int:
        """Значение ресурса в начале (рождение/отдых/левелап)."""
        return 0 if self.resource_type == "rage" else self.max_resource

    def regen_resource(self) -> None:
        """Пассивный реген за боевой ход."""
        rt = self.resource_type
        if rt == "energy":
            self.mp = min(self.max_resource, self.mp + ENERGY_REGEN)
        elif rt == "mana":
            self.mp = min(self.max_resource, self.mp + max(2, int(self.max_resource * 0.04)))
        # ярость пассивно не растёт — копится от ударов

    def gain_rage(self, amount: int) -> None:
        if self.resource_type == "rage":
            self.mp = min(self.max_resource, self.mp + amount)

    def reset_combat_resource(self) -> None:
        """Конец боя: ярость спадает; энергия/мана остаются."""
        if self.resource_type == "rage":
            self.mp = 0

    @property
    def attack_power(self) -> int:
        """Базовый урон обычной атаки = основной атрибут + бонус оружия."""
        prim = self.attr(self.primary_attr)
        weap = 0
        w = self.equipment.get("weapon")
        if w and self.durab("weapon") > 0:
            weap = ITEMS[w].get("bonus", {}).get("atk", 0)
        from . import enchant, pets
        ench = enchant.bonus_atk(self) + pets.atk_bonus(self)
        return int((prim + weap + self.level + ench) * (1 + self._talent("atk_pct") + self.remort_bonus + self.race_passive("atk_pct", 0.0)))

    @property
    def defense(self) -> int:
        d = 0
        for slot, item_key in self.equipment.items():
            if item_key and not (slot in WEAR_SLOTS and self.durab(slot) == 0):
                d += ITEMS[item_key].get("bonus", {}).get("defense", 0)
        from . import enchant, sockets
        return d + self.attr("dex") // 3 + enchant.bonus_def(self) + sockets.stat_bonus(self, "defense")

    @property
    def damage_reduction(self) -> float:
        base = CLASSES[self.cls].get("damage_reduction", 0.0)
        base += self.race_passive("damage_reduction", 0.0)
        base += self._talent("dmgred")
        return min(0.75, base)

    @property
    def crit_chance(self) -> float:
        base = 0.05 + self.attr("dex") * 0.005
        base += CLASSES[self.cls].get("crit_bonus", 0.0)
        base += self.race_passive("crit", 0.0)
        base += self._talent("crit")
        from . import pets
        base += pets.crit_bonus(self)
        # крит с экипировки (аффиксы редких предметов), в процентах
        ecrit = 0
        for slot, k in self.equipment.items():
            if k and not (slot in WEAR_SLOTS and self.durab(slot) == 0):
                ecrit += ITEMS[k].get("bonus", {}).get("crit", 0)
        base += ecrit / 100.0
        from . import sockets
        base += sockets.stat_bonus(self, "crit") / 100.0
        return min(0.75, base)

    @property
    def double_strike(self) -> float:
        return CLASSES[self.cls].get("double_strike", 0.0)

    @property
    def dodge_chance(self) -> float:
        """Пассивный шанс уклонения от расы (поверх скилла Evasion)."""
        return self.race_passive("dodge", 0.0)

    @property
    def lifesteal(self) -> float:
        """Вампиризм: класс (некромант) + раса."""
        return CLASSES[self.cls].get("lifesteal", 0.0) + self.race_passive("lifesteal", 0.0) + self._talent("lifesteal")

    @property
    def xp_mult(self) -> float:
        return self.race_passive("xp_bonus", 1.0)

    @property
    def gold_mult(self) -> float:
        from . import pets
        return self.race_passive("gold_find", 1.0) + pets.gold_bonus(self)

    @property
    def xp_to_next(self) -> int:
        # растянутая кривая под шкалу 1–100: ранние уровни быстрые, дальше круче
        return int(50 * self.level * (1 + self.level / 20))

    # ───────── реморт (престиж) ─────────
    REMORT_BONUS_PER = 0.05    # +5% к атаке и HP за каждый реморт (навсегда)
    REMORT_BONUS_MAX = 0.50    # потолок бонуса: после 10 кругов рост статов
                                # останавливается — дальше реморт только ради
                                # звезды престижа и наград (косметика/слава)

    @property
    def remort_count(self) -> int:
        return int(self.flags.get("remort", 0))

    @property
    def remort_bonus(self) -> float:
        return min(self.REMORT_BONUS_PER * self.remort_count, self.REMORT_BONUS_MAX)

    @property
    def remort_bonus_maxed(self) -> bool:
        """True, если бонус реморта уже упёрся в потолок (дальше — только слава)."""
        return self.REMORT_BONUS_PER * self.remort_count >= self.REMORT_BONUS_MAX

    def remort(self) -> bool:
        """Перерождение на максимальном уровне: сброс уровня/опыта в 1, но
        навсегда +5% к силе (до потолка REMORT_BONUS_MAX). Золото, снаряжение,
        таланты и умения сохраняются."""
        if self.level < LEVEL_CAP:
            return False
        self.flags["remort"] = self.remort_count + 1
        self.level = 1
        self.xp = 0
        self.flags.pop("maxlvl_note", None)
        self.init_vitals()
        return True

    # ───────── умения ─────────
    @property
    def class_basics(self) -> List[str]:
        """Стартовые (базовые) умения класса — известны с рождения."""
        return CLASSES[self.cls].get("skills", [])

    @property
    def skills(self) -> List[str]:
        """Умения, доступные в бою сейчас (боевая панель) = лоудаут."""
        return self.loadout if self.loadout else list(self.class_basics)

    def init_skills(self):
        """При создании: выучить базовые умения класса и слотить их."""
        if not self.learned:
            self.learned = list(self.class_basics)
        if not self.loadout:
            self.loadout = list(self.learned)[:self.LOADOUT_MAX]

    def init_vitals(self):
        """Установить HP в максимум, ресурс — в стартовое значение."""
        self.hp = self.max_hp
        self.mp = self.start_resource()

    # ───────── прочность снаряжения ─────────
    def durab(self, slot: str) -> int:
        if not self.equipment.get(slot):
            return 0
        return int((self.flags.get("durab") or {}).get(slot, DURAB_MAX))

    def set_durab(self, slot: str, val: int):
        self.flags.setdefault("durab", {})[slot] = max(0, min(DURAB_MAX, int(val)))

    def wear(self, slot: str, n: int = 1):
        if self.equipment.get(slot) and slot in WEAR_SLOTS:
            self.set_durab(slot, self.durab(slot) - n)

    def repair_cost(self) -> int:
        cost = 0
        for slot in WEAR_SLOTS:
            if self.equipment.get(slot):
                cost += (DURAB_MAX - self.durab(slot)) * REPAIR_RATE
        return cost

    def repair_all(self):
        for slot in WEAR_SLOTS:
            if self.equipment.get(slot):
                self.set_durab(slot, DURAB_MAX)
