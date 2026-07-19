# -*- coding: utf-8 -*-
"""
Каталог умений: какие умения у какого класса, на каком уровне изучаются,
статус для игрока (выучено / можно выучить / закрыто), обучение у учителя
и управление боевым лоудаутом (до 5 активных умений).
"""
from typing import List, Optional, Tuple

from .content import SKILLS, CLASSES
from .character import Character
from . import money


def class_of(skill_id: str) -> Optional[str]:
    """Класс, которому принадлежит умение (из поля class или из базовых класса)."""
    s = SKILLS.get(skill_id, {})
    if s.get("class"):
        return s["class"]
    for cid, c in CLASSES.items():
        if skill_id in c.get("skills", []):
            return cid
    return None


def is_basic(skill_id: str) -> bool:
    cls = class_of(skill_id)
    return bool(cls) and skill_id in CLASSES[cls].get("skills", [])


def learn_level(skill_id: str) -> int:
    s = SKILLS.get(skill_id, {})
    if s.get("learn_level"):
        return int(s["learn_level"])
    return 1 if is_basic(skill_id) else 1


def learn_cost(skill_id: str) -> int:
    """Цена обучения золотом (0 для базовых)."""
    if is_basic(skill_id):
        return 0
    s = SKILLS.get(skill_id, {})
    if "cost" in s:
        return int(s["cost"]) * 100
    return learn_level(skill_id) * 2000


def all_class_skills(cls: str) -> List[str]:
    """Весь каталог умений класса, отсортирован по уровню изучения, затем по имени."""
    out = [sid for sid in SKILLS if class_of(sid) == cls]
    return sorted(out, key=lambda s: (learn_level(s), SKILLS[s].get("name", s)))


def learnable_now(ch: Character) -> List[str]:
    """Умения, которые игрок может выучить прямо сейчас (уровень подходит, не выучено)."""
    return [s for s in all_class_skills(ch.cls)
            if s not in ch.learned and learn_level(s) <= ch.level]


def locked(ch: Character) -> List[str]:
    """Умения, ещё закрытые по уровню."""
    return [s for s in all_class_skills(ch.cls)
            if s not in ch.learned and learn_level(s) > ch.level]


def status(ch: Character, skill_id: str) -> str:
    if skill_id in ch.learned:
        return "learned"
    if learn_level(skill_id) <= ch.level:
        return "learnable"
    return "locked"


def can_learn(ch: Character, skill_id: str) -> Tuple[bool, str]:
    if class_of(skill_id) != ch.cls:
        return False, "Это умение не вашего класса."
    if skill_id in ch.learned:
        return False, "Умение уже выучено."
    if learn_level(skill_id) > ch.level:
        return False, f"Нужен уровень {learn_level(skill_id)}."
    cost = learn_cost(skill_id)
    if ch.gold < cost:
        return False, f"Нужно {money.fmt(cost)}, у вас {money.fmt(ch.gold)}."
    return True, ""


def learn(ch: Character, skill_id: str) -> Tuple[bool, str]:
    ok, why = can_learn(ch, skill_id)
    if not ok:
        return False, why
    ch.gold -= learn_cost(skill_id)
    ch.learned.append(skill_id)
    name = SKILLS[skill_id]["name"]
    # авто-слот, если в лоудауте есть место
    if len(ch.loadout) < ch.LOADOUT_MAX and skill_id not in ch.loadout:
        ch.loadout.append(skill_id)
        return True, f"📖 Изучено: *{name}*! Добавлено в боевую панель."
    return True, f"📖 Изучено: *{name}*! Добавьте его в панель через «Умения»."


def toggle_loadout(ch: Character, skill_id: str) -> Tuple[bool, str]:
    """Добавить/убрать умение из боевой панели (до 5)."""
    if skill_id not in ch.learned:
        return False, "Сначала выучите это умение."
    if skill_id in ch.loadout:
        ch.loadout.remove(skill_id)
        return True, f"➖ Убрано из панели: {SKILLS[skill_id]['name']}."
    if len(ch.loadout) >= ch.LOADOUT_MAX:
        return False, f"В панели уже {ch.LOADOUT_MAX} умений — уберите одно."
    ch.loadout.append(skill_id)
    return True, f"➕ В панель: {SKILLS[skill_id]['name']}."


# ───────── пресеты боевой панели (наборы умений) ─────────
def save_preset(ch, slot) -> None:
    presets = ch.flags.setdefault("presets", {})
    presets[str(slot)] = list(ch.loadout)


def preset_exists(ch, slot) -> bool:
    return str(slot) in (ch.flags.get("presets") or {})


def load_preset(ch, slot) -> bool:
    p = (ch.flags.get("presets") or {}).get(str(slot))
    if not p:
        return False
    ch.loadout = [s for s in p if s in ch.learned][:ch.LOADOUT_MAX]
    return True
