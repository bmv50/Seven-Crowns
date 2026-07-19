# -*- coding: utf-8 -*-
"""Питомцы и маунты с прогрессией.
Питомцы получают опыт за убийства владельца, растут в уровне и дают пассивные
бонусы (атака, крит). Маунт даёт бонус к добыче золота. Хранение:
  ch.flags["pets"]   = {pet_id: {"lvl":N,"xp":M}}
  ch.flags["pet"]    = активный pet_id | None
  ch.flags["mount"]  = активный mount_id | None  (и список владения в pets-словаре mounts_owned)
  ch.flags["mounts"] = [mount_id, ...] (купленные)
"""
from . import content

_CFG = content._load_optional("pets.yaml") or {}
PETS = _CFG.get("pets", {})
MOUNTS = _CFG.get("mounts", {})

PET_CAP = 30


def owned_pets(ch) -> dict:
    return ch.flags.setdefault("pets", {})


def owned_mounts(ch) -> list:
    return ch.flags.setdefault("mounts", [])


def active_pet(ch):
    pid = ch.flags.get("pet")
    return pid if pid in owned_pets(ch) else None


def active_mount(ch):
    mid = ch.flags.get("mount")
    return mid if mid in owned_mounts(ch) else None


def pet_level(ch, pid: str) -> int:
    return int(owned_pets(ch).get(pid, {}).get("lvl", 0))


def xp_to_next(lvl: int) -> int:
    return 30 * lvl


def adopt_pet(ch, pid: str):
    cfg = PETS.get(pid)
    if not cfg:
        return False, "Питомец не найден."
    if pid in owned_pets(ch):
        return False, "Этот питомец уже у вас."
    if ch.gold < cfg["cost"]:
        return False, "Не хватает золота."
    ch.gold -= cfg["cost"]
    owned_pets(ch)[pid] = {"lvl": 1, "xp": 0}
    ch.flags.setdefault("pet", pid)   # первый питомец становится активным
    return True, f"{cfg['emoji']} {cfg['name']} теперь с вами!"


def buy_mount(ch, mid: str):
    cfg = MOUNTS.get(mid)
    if not cfg:
        return False, "Маунт не найден."
    if mid in owned_mounts(ch):
        return False, "Этот маунт уже у вас."
    if ch.gold < cfg["cost"]:
        return False, "Не хватает золота."
    ch.gold -= cfg["cost"]
    owned_mounts(ch).append(mid)
    ch.flags.setdefault("mount", mid)
    return True, f"{cfg['emoji']} {cfg['name']} оседлан!"


def set_active_pet(ch, pid: str):
    if pid in owned_pets(ch):
        ch.flags["pet"] = pid
        return True
    return False


def set_active_mount(ch, mid: str):
    if mid in owned_mounts(ch):
        ch.flags["mount"] = mid
        return True
    return False


# ── бонусы в бою/экономике ──
def atk_bonus(ch) -> int:
    pid = active_pet(ch)
    if not pid:
        return 0
    return pet_level(ch, pid) * PETS.get(pid, {}).get("atk_per_level", 0)


def crit_bonus(ch) -> float:
    pid = active_pet(ch)
    if not pid:
        return 0.0
    cfg = PETS.get(pid, {})
    if pet_level(ch, pid) >= cfg.get("crit_at", 99):
        return cfg.get("crit_bonus", 0.0)
    return 0.0


def gold_bonus(ch) -> float:
    mid = active_mount(ch)
    if not mid:
        return 0.0
    return MOUNTS.get(mid, {}).get("gold_bonus", 0.0)


def on_kill_xp(ch, amount: int):
    """Активный питомец получает опыт за убийство. Возвращает строки о росте."""
    pid = active_pet(ch)
    if not pid:
        return []
    d = owned_pets(ch)[pid]
    d["xp"] = int(d.get("xp", 0)) + max(1, amount)
    out = []
    cfg = PETS.get(pid, {})
    while d["lvl"] < PET_CAP and d["xp"] >= xp_to_next(d["lvl"]):
        d["xp"] -= xp_to_next(d["lvl"])
        d["lvl"] += 1
        msg = f"{cfg.get('emoji','🐾')} {cfg.get('name','Питомец')} вырос до уровня {d['lvl']}!"
        if d["lvl"] == cfg.get("crit_at"):
            msg += " 🎯 Открыт бонус крита!"
        out.append(msg)
    return out


def render(ch) -> str:
    lines = ["🐾 *Питомцы и маунты*"]
    ap = active_pet(ch); am = active_mount(ch)
    pets = owned_pets(ch)
    if pets:
        lines.append("\n*Питомцы:*")
        for pid, d in pets.items():
            cfg = PETS.get(pid, {})
            mark = " ✅" if pid == ap else ""
            lines.append(f"{cfg.get('emoji','🐾')} {cfg.get('name',pid)} — ур.{d['lvl']} "
                         f"({d['xp']}/{xp_to_next(d['lvl'])}){mark}")
    mounts = owned_mounts(ch)
    if mounts:
        lines.append("\n*Маунты:*")
        for mid in mounts:
            cfg = MOUNTS.get(mid, {})
            mark = " ✅" if mid == am else ""
            lines.append(f"{cfg.get('emoji','🐎')} {cfg.get('name',mid)} "
                         f"(+{int(cfg.get('gold_bonus',0)*100)}% золота){mark}")
    if not pets and not mounts:
        lines.append("У вас пока нет спутников. Купите их в меню ниже.")
    return "\n".join(lines)
