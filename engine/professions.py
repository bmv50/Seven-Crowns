# -*- coding: utf-8 -*-
"""Профессии-добыча (горное дело/травничество/рыбалка) с уровнями навыка.
Узлы добычи привязаны к комнатам (data/professions.yaml). Сбор даёт предмет
и опыт навыка; шанс успеха зависит от уровня навыка относительно требования.
Данные игрока в ch.flags["prof"] = {prof_id: {"lvl":N,"xp":M}}.
"""
import time
from . import content

_CFG = content._load_optional("professions.yaml") or {}
PROFS = _CFG.get("professions", {})
NODES = _CFG.get("nodes", {})

SKILL_CAP = 50


def nodes_in(room: str):
    return NODES.get(room, [])


def _pdata(ch, prof: str) -> dict:
    return ch.flags.setdefault("prof", {}).setdefault(prof, {"lvl": 1, "xp": 0})


def is_learned(ch, prof: str) -> bool:
    return prof in ch.flags.get("prof", {})


def learn(ch, prof: str) -> bool:
    """Освоить профессию у наставника (с нуля). True, если впервые."""
    if prof not in PROFS:
        return False
    pd = ch.flags.setdefault("prof", {})
    if prof in pd:
        return False
    pd[prof] = {"lvl": 1, "xp": 0}
    return True


def level(ch, prof: str) -> int:
    d = ch.flags.get("prof", {}).get(prof)
    return int(d["lvl"]) if d else 0


def xp_to_next(lvl: int) -> int:
    return 50 * lvl


def success_chance(lvl: int, req: int) -> float:
    return max(0.5, min(0.95, 0.6 + 0.05 * (lvl - req)))


def cooldown_left(ch, room: str, idx: int) -> int:
    node = nodes_in(room)[idx]
    key = f"{room}:{idx}"
    last = (ch.flags.get("gather_cd") or {}).get(key, 0)
    left = int(last + node.get("cd", 60) - time.time())
    return max(0, left)


def gather(ch, room: str, idx: int):
    """Попытка добычи. Возвращает (status, lines). status: ok/fail/cd/locked/none."""
    nodes = nodes_in(room)
    if idx < 0 or idx >= len(nodes):
        return "none", ["Здесь нечего добывать."]
    node = nodes[idx]
    prof = node["prof"]
    if not is_learned(ch, prof):
        return "locked", [f"🔒 Сначала освойте «{PROFS.get(prof,{}).get('name',prof)}» у наставника."]
    lvl = level(ch, prof)
    if lvl < node.get("skill_req", 1):
        return "locked", [f"🔒 Нужен навык «{PROFS.get(prof,{}).get('name',prof)}» ур.{node['skill_req']}."]
    left = cooldown_left(ch, room, idx)
    if left > 0:
        return "cd", [f"⏳ Узел истощён, восстановится через {left} сек."]
    ch.flags.setdefault("gather_cd", {})[f"{room}:{idx}"] = time.time()
    import random
    pmeta = PROFS.get(prof, {})
    iname = content.ITEMS.get(node["item"], {}).get("name", node["item"])
    if random.random() > success_chance(lvl, node.get("skill_req", 1)):
        return "fail", [f"{pmeta.get('emoji','⛏')} Добыть {iname} не удалось — попробуйте снова."]
    ch.inventory.append(node["item"])
    out = [f"{pmeta.get('emoji','⛏')} Добыто: *{iname}*!"]
    # опыт навыка
    d = _pdata(ch, prof)
    d["xp"] += int(node.get("xp", 10))
    while d["lvl"] < SKILL_CAP and d["xp"] >= xp_to_next(d["lvl"]):
        d["xp"] -= xp_to_next(d["lvl"])
        d["lvl"] += 1
        out.append(f"⬆️ {pmeta.get('name',prof)}: навык вырос до {d['lvl']}!")
    return "ok", out


def render(ch) -> str:
    """Сводка профессий игрока."""
    lines = ["🛠 *Профессии*"]
    prof = ch.flags.get("prof", {})
    if not prof:
        lines.append("Пока ничего не освоено. Ищите узлы добычи в мире.")
    for pid, meta in PROFS.items():
        d = prof.get(pid)
        if d:
            lines.append(f"{meta['emoji']} {meta['name']}: ур.{d['lvl']} ({d['xp']}/{xp_to_next(d['lvl'])})")
    return "\n".join(lines)
