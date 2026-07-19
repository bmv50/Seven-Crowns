# -*- coding: utf-8 -*-
"""
Достижения и титулы. Хранятся в ch.flags: kills (всего убийств), achv (список
выполненных id), title (текущий титул). Проверяются после боёв/левелапа/квестов.
"""
from . import content
from . import money

ACHV = content._load_optional("achievements.yaml")


def _value(ch, typ: str) -> int:
    if typ == "kills":
        return int(ch.flags.get("kills", 0))
    if typ == "level":
        return ch.level
    if typ == "gold":
        return ch.gold
    if typ == "quests":
        return sum(1 for v in ch.quests.values() if v == "done")
    return 0


def check(ch):
    """Выдать новые достижения. -> список строк для сообщения."""
    earned = set(ch.flags.get("achv", []))
    out = []
    for aid, a in ACHV.items():
        if aid in earned:
            continue
        if _value(ch, a.get("type", "")) >= a.get("target", 1):
            earned.add(aid)
            g = a.get("gold", 0)
            ch.gold += g
            if a.get("title") and not ch.flags.get("title"):
                ch.flags["title"] = a["title"]   # первый титул надевается сам
            reward = f" Титул: «{a['title']}»." if a.get("title") else ""
            if g:
                reward += f" +{money.fmt(g)}."
            out.append(f"🏆 *Достижение: {a['name']}*!{reward}")
    if out:
        ch.flags["achv"] = list(earned)
    return out


def render(ch) -> str:
    earned = set(ch.flags.get("achv", []))
    L = [f"🏆 *Достижения* — {len(earned)}/{len(ACHV)}", ""]
    for aid, a in ACHV.items():
        if aid in earned:
            L.append(f"✅ *{a['name']}* — _{a['desc']}_")
        else:
            L.append(f"▫️ {a['name']} ({_value(ch, a['type'])}/{a['target']}) — _{a['desc']}_")
    return "\n".join(L)


def titled_achievements(ch):
    """[(aid, title)] для заработанных достижений, у которых есть титул."""
    earned = set(ch.flags.get("achv", []))
    return [(aid, ACHV[aid]["title"]) for aid in ACHV
            if aid in earned and ACHV[aid].get("title")]


def _valid_titles(ch):
    """Все титулы, доступные игроку: за достижения + за собранные коллекции бестиария."""
    return {tt for _, tt in titled_achievements(ch)} | set(ch.flags.get("extra_titles", []))


def active_title(ch):
    """Текущий выбранный титул (или None, если снят/не заработан)."""
    t = ch.flags.get("title")
    return t if t in _valid_titles(ch) else None


def set_title(ch, title):
    """Выбрать титул к показу. title пустой/None → снять. Возвращает True при успехе."""
    if not title:
        ch.flags.pop("title", None)
        return True
    if title in _valid_titles(ch):
        ch.flags["title"] = title
        return True
    return False


def name_tag(ch):
    """⭐N «Титул» Имя (ур.N) — для показа в комнате/карточке.
    Префикс ⭐N — метка престижа (число ремортов), отображается только
    если игрок хоть раз переродился (remort_count > 0)."""
    t = active_title(ch)
    base = f"{ch.name} (ур.{ch.level})"
    tagged = f"«{t}» {base}" if t else base
    rc = ch.remort_count
    return f"⭐{rc} {tagged}" if rc > 0 else tagged
