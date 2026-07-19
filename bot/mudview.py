# -*- coding: utf-8 -*-
"""
Рендер комнат и карточки персонажа в «классическом» MUD-стиле
(богатый текст под фото). Вынесено в отдельный модуль для надёжности.
"""
import re
from engine.content import WORLD, ITEMS, CLASSES, RACES
from engine import npc as npclib
from engine import achievements as _ach
from engine import combat as _combat
from engine import rules2 as _r2
from engine import money as _money
from engine.world import ground_items_for
from bot import mudnames as cmds

DIFF_EMOJI = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
SECTOR_EMOJI = {"город": "🏙", "city": "🏙", "лес": "🌲", "подземелье": "🕳",
                "горы": "⛰", "болото": "🌫", "вода": "🌊"}
_CAT_EMO = {"undead": "💀", "demon": "😈", "fire": "🔥", "ice": "❄️",
            "spirit": "👻", "construct": "🗿", "beast": "🐾"}
_DT_RU = {"fire": "огонь", "cold": "холод", "holy": "свет", "poison": "яд",
          "bash": "дробящ.", "pierce": "колющ.", "slash": "режущ.",
          "lightning": "молния", "acid": "кислота", "negative": "тьма",
          "energy": "энергия", "mental": "разум", "disease": "болезнь", "light": "свет"}


def clean_desc(text: str) -> str:
    """Убрать жёсткие внутри-абзацные переносы (вёрстка YAML), оставив
    разбивку на абзацы — пусть Telegram переносит по ширине экрана сам."""
    paras = re.split(r"\n\s*\n", (text or "").strip())
    return "\n\n".join(" ".join(p.split()) for p in paras if p.strip())


def _bar(cur, mx, length=10, fill="█", empty="░"):
    cur = max(0, cur)
    f = int((cur / mx) * length) if mx else 0
    f = max(0, min(length, f))
    return fill * f + empty * (length - f)


def _room_flag(room: dict) -> str:
    if room.get("safe"):
        return "PEACEFUL"
    if room.get("arena"):
        return "ARENA"
    return "WILD"


def _exit_hint(dest_id: str) -> str:
    """Короткая подсказка по соседней комнате: NPC-роль или зона + флаг."""
    room = WORLD.get(dest_id, {})
    bits = []
    npcs = room.get("npc", [])
    if npcs:
        n0 = npcs[0]
        role = npclib.role_label(n0)
        nm = room.get("name", dest_id)
        bits.append(f"{nm} ({role})")
    else:
        bits.append(room.get("name", dest_id))
    bits.append(_room_flag(room))
    return ", ".join(bits)


def render_room(ch, world, others) -> str:
    r = WORLD[ch.room]
    L = [f"📍 *{r['name']}*", "", clean_desc(r.get("desc", "")), ""]

    mobs = world.living_in(ch.room)
    for m in mobs:
        df = DIFF_EMOJI[_combat.mob_difficulty(ch.level, m.meta.get("level", 1))]
        # метка эндгейм-угрозы: мобы уровня >65 (например, предвечная_бездна ур.68)
        _skull = "☠️" if m.meta.get("level", 1) > 65 else ""
        L.append(f"{df}{_skull} {m.meta['emoji']} *{m.meta['name']}* "
                 f"ур.{m.meta.get('level',1)} [{_bar(m.hp, m.max_hp, 8)}]")
        if _r2.ENABLED:
            p = _r2.mob_profile(m.meta)
            hint = []
            if p["category"] != "default":
                hint.append(_CAT_EMO.get(p["category"], ""))
            if p["vuln"]:
                hint.append("уязв: " + ",".join(_DT_RU.get(t, t) for t in sorted(p["vuln"])))
            if p["immune"]:
                hint.append("имм: " + ",".join(_DT_RU.get(t, t) for t in sorted(p["immune"])))
            if any(hint):
                L.append("   _" + " · ".join(x for x in hint if x) + "_")
    if mobs:
        L.append("")

    ground = ground_items_for(ch, ch.room)
    if ground:
        L.append("✨ *На земле:* " + ", ".join(cmds.item_label(i) for i in ground))
    for c in world.corpses_in(ch.room):
        if c.get("loot"):
            L.append(f"💀 Тело: {c['emoji']} {c['name']} — `get тело`")

    party = [o for o in others if o.uid != ch.uid]
    if party:
        L.append("🧑‍🤝‍🧑 *Рядом игроки:*")
        for o in party:
            L.append(f"  {CLASSES[o.cls]['emoji']} {_ach.name_tag(o)}")

    # выходы: русское(english) название стороны света
    ex_lbl = [f"{d.capitalize()}({cmds.DIR_RU2EN.get(d, d)})" for d in r["exits"]]
    L.append("")
    L.append("🧭 *Выходы:* " + " | ".join(ex_lbl))
    sect = (r.get("zone") or "").lower()
    emo = next((e for k, e in SECTOR_EMOJI.items() if k in sect), "🗺")
    L.append(f"🗺 *Местность:* {emo} {r.get('zone','—')}  ·  {_room_flag(r)}")
    try:
        from engine import events as _ev
        if _ev.ENABLED and _ev.banner():
            L.append(_ev.banner())
    except Exception:
        pass

    # «Куда направишься?»
    cls_ru = CLASSES[ch.cls]["name"].lower()
    L.append("")
    L.append("—")
    L.append(f"*Куда направишься, {cls_ru}?*")
    for d, dest in r["exits"].items():
        en = cmds.DIR_RU2EN.get(d, d)
        dest_name = WORLD.get(dest, {}).get("name", dest)
        L.append(f"*{d.capitalize()}*(`{en}`) — {dest_name}")
    return "\n".join(L)


def _bar10(cur, mx, fill="🟩"):
    """Шкала из 10 клеток. fill — цвет заполнения (🟥 HP, 🟧 ярость, 🟦 мана,
    🟩 опыт, 🟨 энергия). Клетки-эмодзи одинаковой ширины, поэтому шкалы,
    стоящие В НАЧАЛЕ строки, выравниваются идеально (фидбек владельца:
    подписи разной длины перед шкалой разваливали выравнивание).
    Сплошных ЦВЕТНЫХ полос в тексте Telegram не существует — сплошные
    символы (█▰) монохромны; цвет дают только клетки-эмодзи."""
    cur = max(0, cur); mx = max(1, mx)
    f = max(0, min(10, int(cur / mx * 10)))
    return fill * f + "⬛" * (10 - f)


# цвет шкалы ресурса по его типу (у классов ресурсы разные)
_RES_FILL = {"Мана": "🟦", "Ярость": "🟧", "Энергия": "🟨"}


def _res_fill(name: str) -> str:
    return _RES_FILL.get(name, "🟦")


def render_score(ch) -> str:
    """Красочная карточка героя с эмодзи (мобильно-безопасная)."""
    c = CLASSES[ch.cls]; rc = RACES[ch.race]
    L = []
    title = _ach.active_title(ch)
    prestige = f"⭐{ch.remort_count} " if ch.remort_count > 0 else ""
    head = f"{rc.get('emoji','🧝')}{c.get('emoji','⚔️')} {prestige}*{ch.name}*"
    if title:
        head += f"  🎖 _{title}_"
    L.append(head)
    L.append(f"{rc['name']} · {c['name']}  ·  ⭐ *Уровень {ch.level}*")
    L.append("━━━━━━━━━━━━━━━━━━")

    # шкалы: полоса всегда с начала строки (клетки равной ширины → ровные
    # столбцы), подпись и цифры — после; цвет по ресурсу (HP 🟥, мана 🟦,
    # ярость 🟧, опыт 🟩)
    L.append(f"{_bar10(ch.hp, ch.max_hp, '🟥')} ❤️ {ch.hp}/{ch.max_hp}")
    L.append(f"{_bar10(ch.mp, ch.max_resource, _res_fill(ch.resource_name))} "
             f"{ch.resource_emoji} {ch.mp}/{ch.max_resource}")
    L.append(f"{_bar10(ch.xp, ch.xp_to_next, '🟩')} ✨ {ch.xp}/{ch.xp_to_next}")
    L.append("━━━━━━━━━━━━━━━━━━")

    # боевые
    combat_bits = [f"⚔️ Атака *{ch.attack_power}*", f"🛡 Защита *{ch.defense}*",
                   f"💥 Крит *{int(ch.crit_chance*100)}%*"]
    if getattr(ch, "lifesteal", 0):
        combat_bits.append(f"🩸 Вампиризм {int(ch.lifesteal*100)}%")
    if getattr(ch, "dodge_chance", 0):
        combat_bits.append(f"💨 Уклон {int(ch.dodge_chance*100)}%")
    L.append("  ".join(combat_bits))
    # атрибуты
    L.append(f"💪 Сила *{ch.attr('str')}*   🤸 Ловк *{ch.attr('dex')}*   "
             f"🧠 Инт *{ch.attr('int')}*   🕯 Дух *{ch.attr('spi')}*")
    L.append(f"💰 *Монеты:* {_money.fmt(ch.gold)}")

    # статусные строки (только если есть что показать)
    rested = int(ch.flags.get("rested", 0))
    if rested:
        L.append(f"💤 Отдохнувший опыт: {rested}")
    if _r2.ENABLED:
        _av = _r2.alignment(ch)
        _al = {"good": "☀️ Светлый путь", "evil": "🌑 Тёмный путь",
               "neutral": "⚖️ Нейтралитет"}[_r2.align_label(_av)]
        L.append(f"🧭 Мировоззрение: {_al} ({_av})")
    try:
        from engine import karma as _km
        _kl = _km.line(ch)
        if _kl:
            L.append(_kl)
    except Exception:
        pass
    try:
        from engine import arena as _ar
        if _ar.has_played(ch):
            _rt = _ar.rating(ch)
            L.append(f"🏟 Арена: {_rt} {_ar.tier(_rt)}")
    except Exception:
        pass
    try:
        from engine import seasons as _se
        if _se.ENABLED:
            _nm, _em = _se.tier(_se.points(ch))
            L.append(f"🏅 Сезон: {_em} {_nm} · {_se.points(ch)} очк.")
    except Exception:
        pass

    # снаряжение
    eq = [cmds.item_label(it) for slot in ("weapon", "armor", "shield")
          if (it := ch.equipment.get(slot))]
    if eq:
        L.append("━━━━━━━━━━━━━━━━━━")
        L.append("🎽 *Снаряжение:*")
        L.extend(f"   • {x}" for x in eq)
    return "\n".join(L)
