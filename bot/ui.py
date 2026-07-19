# -*- coding: utf-8 -*-
"""Рендеры текста и inline-клавиатуры."""
from typing import List
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from engine import content
from engine import npc as npclib
from engine import skills as skillmod
from engine import quest as _quest_brief
from engine.content import CLASSES, SKILLS, ITEMS, WORLD, MOBS, RACES, SHOPS
from engine import money
from engine import combat as _combat
from engine import arena as _arena
from engine import talents as _talents
from engine import uigate as _uigate

DIFF_EMOJI = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
from engine.character import Character
from engine.world import World, MobInstance, ground_items_for


def bar(cur, mx, length=10, fill="█", empty="░"):
    cur = max(0, cur)
    f = int((cur / mx) * length) if mx else 0
    f = max(0, min(length, f))
    return fill * f + empty * (length - f)


# ───────────────────── РЕНДЕРЫ ─────────────────────
def render_room(ch: Character, world: World, others: List[Character]) -> str:
    from bot import mudview
    return mudview.render_room(ch, world, others)


def _render_room_legacy(ch: Character, world: World, others: List[Character]) -> str:
    r = WORLD[ch.room]
    L = [f"📍 *{r['name']}*", f"_{r['zone']}_", "", r["desc"].strip(), ""]
    mobs = world.living_in(ch.room)
    from engine import rules2 as _r2
    _CAT_EMO = {"undead": "💀", "demon": "😈", "fire": "🔥", "ice": "❄️",
                "spirit": "👻", "construct": "🗿", "beast": "🐾"}
    _DT_RU = {"fire": "огонь", "cold": "холод", "holy": "свет", "poison": "яд",
              "bash": "дробящий", "pierce": "колющий", "slash": "режущий",
              "lightning": "молния", "acid": "кислота", "negative": "тьма",
              "energy": "энергия", "mental": "разум", "disease": "болезнь", "light": "свет"}
    for m in mobs:
        df = DIFF_EMOJI[_combat.mob_difficulty(ch.level, m.meta.get("level", 1))]
        L.append(f"{df} {m.meta['emoji']} *{m.meta['name']}* ур.{m.meta.get('level',1)} "
                 f"[{bar(m.hp, m.max_hp, 8)}] {m.hp}/{m.max_hp}")
        if _r2.ENABLED:
            _p = _r2.mob_profile(m.meta)
            _hint = []
            if _p["category"] != "default":
                _hint.append(_CAT_EMO.get(_p["category"], ""))
            if _p["vuln"]:
                _hint.append("уязв: " + ", ".join(_DT_RU.get(t, t) for t in sorted(_p["vuln"])))
            if _p["immune"]:
                _hint.append("имм: " + ", ".join(_DT_RU.get(t, t) for t in sorted(_p["immune"])))
            if _hint:
                L.append("   _" + " · ".join(x for x in _hint if x) + "_")
    if mobs:
        L.append("")
    if r.get("npc"):
        L.append("👤 *Здесь находятся:*")
        for n in r["npc"]:
            L.append(f"   {npclib.emoji(n)} {npclib.display_name(n)} — {npclib.role_label(n)}")
    if r.get("items"):
        L.append("✨ На земле: " + ", ".join(ITEMS[i]["name"] for i in r["items"]))
    for c in world.corpses_in(ch.room):
        if c.get("loot"):
            L.append(f"💀 Тело: {c['emoji']} {c['name']} — можно обыскать")
    party = [o for o in others if o.uid != ch.uid]
    if party:
        L.append("🧑‍🤝‍🧑 Рядом: " + ", ".join(
            f"{CLASSES[o.cls]['emoji']}{o.name}(ур.{o.level})" for o in party))
    L.append(f"🚪 Выходы: {', '.join(r['exits'].keys())}")
    for qline in _quest_brief.active_brief(ch):
        L.append(qline)
    return "\n".join(L)


def render_stats(ch: Character) -> str:
    from bot import mudview
    return mudview.render_score(ch)


def _render_stats_legacy(ch: Character) -> str:
    c = CLASSES[ch.cls]
    r = RACES[ch.race]
    eq = ch.equipment
    def _eqd(slot):
        if not eq.get(slot):
            return "—"
        nm = ITEMS[eq[slot]]["name"]
        dv = ch.durab(slot)
        return f"{nm} [{dv}/100]" + (" ⚠️СЛОМАНО" if dv == 0 else "")
    _SLOT_RU = {"weapon": "🗡 Оружие", "shield": "🛡 Щит", "armor": "🥋 Броня",
                "head": "⛑ Голова", "hands": "🧤 Кисти", "legs": "👖 Ноги",
                "feet": "🥾 Стопы", "cloak": "🧥 Плащ", "belt": "🎗 Пояс",
                "neck": "📿 Шея", "ring1": "💍 Кольцо I", "ring2": "💍 Кольцо II",
                "wrist": "⌚ Запястье", "accessory": "💠 Аксессуар"}
    _eq_lines = []
    for _slot, _lbl in _SLOT_RU.items():
        _it = eq.get(_slot)
        if _slot in ("weapon", "armor"):
            _eq_lines.append(f"{_lbl}: {_eqd(_slot)}")
        elif _it:
            _eq_lines.append(f"{_lbl}: {ITEMS[_it]['name']}")
    equip_block = "\n".join(_eq_lines)
    # строка пассивок
    extras = []
    if ch.lifesteal: extras.append(f"🩸{ch.lifesteal:.0%}")
    if ch.dodge_chance: extras.append(f"💨{ch.dodge_chance:.0%}")
    if ch.xp_mult != 1.0: extras.append(f"✨×{ch.xp_mult}")
    if ch.gold_mult != 1.0: extras.append(f"💰×{ch.gold_mult}")
    extra_line = ("  " + " ".join(extras)) if extras else ""
    title = ch.flags.get("title")
    title_line = f"🎖 _{title}_\n" if title else ""
    rested = int(ch.flags.get("rested", 0))
    rest_line = f"💤 Отдохнувший опыт: {rested}\n" if rested else ""
    arena_line = (f"🏟 Арена: {_arena.rating(ch)} {_arena.tier(_arena.rating(ch))}\n"
                  if _arena.has_played(ch) else "")
    from engine import karma as _karma
    _kl = _karma.line(ch)
    karma_line = (_kl + "\n") if _kl else ""
    from engine import rules2 as _r2s
    align_line = ""
    if _r2s.ENABLED:
        _av = _r2s.alignment(ch)
        _al = {"good": "☀️ Добро", "evil": "🌑 Зло", "neutral": "⚖️ Нейтралитет"}[_r2s.align_label(_av)]
        align_line = f"{_al} ({_av})\n"
    return (
        f"{r['emoji']}{c['emoji']} *{ch.name}*\n"
        f"{title_line}"
        f"{r['name']} {c['name']}, уровень {ch.level}\n"
        f"{rest_line}{arena_line}{karma_line}{align_line}"
        f"❤️ HP [{bar(ch.hp, ch.max_hp)}] {ch.hp}/{ch.max_hp}\n"
        f"{ch.resource_emoji} {ch.resource_name} [{bar(ch.mp, ch.max_resource)}] {ch.mp}/{ch.max_resource}\n"
        f"✨ Опыт: {ch.xp}/{ch.xp_to_next}\n\n"
        f"💪 Сила {ch.attr('str')}  🤸 Ловк {ch.attr('dex')}  "
        f"🧠 Инт {ch.attr('int')}  🕯 Дух {ch.attr('spi')}\n"
        f"⚔️ Атака {ch.attack_power}  🛡 Защита {ch.defense}  "
        f"💥 Крит {ch.crit_chance:.0%}{extra_line}\n"
        f"💰 {money.fmt(ch.gold)}\n\n"
        f"{equip_block}"
    )


def render_inventory(ch: Character) -> str:
    if not ch.inventory:
        return "🎒 Сумка пуста."
    counts = {}
    for it in ch.inventory:
        counts[it] = counts.get(it, 0) + 1
    L = ["🎒 *Сумка:*", "_Нажми на предмет: надеть / снять / использовать._", ""]
    for it, c in counts.items():
        meta = ITEMS[it]
        cc = f" x{c}" if c > 1 else ""
        L.append(f"{meta.get('emoji','•')} {meta['name']}{cc} — {meta['desc']}")
    return "\n".join(L)


def render_races() -> str:
    L = ["🧬 *Выбери свою расу:*", ""]
    for rid, r in RACES.items():
        L.append(f"{r['emoji']} *{r['name']}*\n_{r['desc']}_\n")
    return "\n".join(L)


def class_card(cid: str) -> str:
    """Карточка класса для витрины выбора (Этап 4.2): роль, сложность звёздами,
    стиль игры, бейдж «новичку», плюсы списком и предупреждение о ресурсе
    (если у класса нетривиальное управление ресурсом — мана/ярость)."""
    c = CLASSES[cid]
    lines = [f"{c['emoji']} *{c['name']}*"]
    badge = " ⭐ новичку" if c.get("newbie_ok") else ""
    lines.append(f"_{c['desc']}_{badge}")
    role = c.get("role")
    diff = c.get("difficulty")
    style = c.get("style")
    if role or diff:
        stars = "⭐" * int(diff or 0)
        lines.append(f"Роль: {role or '—'}   Сложность: {stars or '—'}")
    if style:
        lines.append(f"_{style}_")
    for p in c.get("pros") or []:
        lines.append(f"  ✅ {p}")
    if c.get("resource_note"):
        lines.append(f"  {c['resource_note']}")
    return "\n".join(lines)


def render_classes(race: str = None) -> str:
    L = ["⚜️ *Выбери свой класс:*", ""]
    allowed = RACES[race]["allowed_classes"] if race else list(CLASSES.keys())
    for cid in allowed:
        L.append(class_card(cid))
        L.append("")
    return "\n".join(L)


# ───────────────────── КЛАВИАТУРЫ ─────────────────────
def _gated_btn(text: str, callback_data: str, feature: str, level: int):
    """Кнопка гейтованной фичи (Этап 4.2, прогрессивный UI — engine/uigate.py):
    открыта — обычная кнопка; скоро откроется (≤GATE_PREVIEW уровней) — кнопка
    с 🔒 и уровнем открытия, callback ведёт на "locked:<feature>"; иначе —
    None (кнопку не показываем вовсе, рано)."""
    if _uigate.unlocked(feature, level):
        return InlineKeyboardButton(text=text, callback_data=callback_data)
    if _uigate.next_unlock_visible(feature, level):
        label = text.split(" ", 1)[-1] if " " in text else text
        min_level = _uigate.FEATURES[feature]
        return InlineKeyboardButton(text=f"🔒 {label} (с {min_level} ур.)",
                                    callback_data=f"locked:{feature}")
    return None


def kb_titles(ch) -> InlineKeyboardMarkup:
    """Выбор титула к показу рядом с именем (из заработанных достижений
    и из собранных коллекций бестиария — engine/bestiary.py)."""
    from engine import achievements as _a
    rows = []
    shown = set()
    active = _a.active_title(ch)
    for _aid, title in _a.titled_achievements(ch):
        if title in shown:
            continue
        shown.add(title)
        mark = "✅ " if title == active else "🎖 "
        rows.append([InlineKeyboardButton(text=f"{mark}{title}",
                                          callback_data=f"settitle:{title}")])
    for title in ch.flags.get("extra_titles", []):
        if title in shown:
            continue
        shown.add(title)
        mark = "✅ " if title == active else "📖 "
        rows.append([InlineKeyboardButton(text=f"{mark}{title}",
                                          callback_data=f"settitle:{title}")])
    if active:
        rows.append([InlineKeyboardButton(text="🚫 Скрыть титул", callback_data="settitle:")])
    if not rows:
        rows.append([InlineKeyboardButton(
            text="Титулов пока нет — зарабатывайте достижения", callback_data="noop")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="stats")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_season(ch) -> InlineKeyboardMarkup:
    """Экран сезона: кнопка «Забрать» на каждую достигнутую, но не забранную
    ступень сезонного трека, плюс «Назад»."""
    from engine import seasons as _seasons
    rows = []
    for thr in _seasons.track_claimable(ch):
        rows.append([InlineKeyboardButton(text=f"🎁 Забрать {thr}",
                                          callback_data=f"strack:{thr}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="stats")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_back(data: str = "look") -> InlineKeyboardMarkup:
    """Одна кнопка «Назад» — возврат в главное меню (или иной экран)."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⬅️ Назад", callback_data=data)]])


def kb_player(ch: Character) -> InlineKeyboardMarkup:
    """Меню Героя: сумка и умения вынесены сюда (чтобы разгрузить главное меню).
    Таланты/Профессии/Сезон — гейтованные фичи (Этап 4.2, engine/uigate.py):
    заперты кнопкой с 🔒 до GATE_PREVIEW уровней, дальше не показываются вовсе."""
    lvl = ch.level
    items = [
        InlineKeyboardButton(text="🎒 Сумка", callback_data="inv"),
        InlineKeyboardButton(text="📜 Умения", callback_data="skills"),
        InlineKeyboardButton(text="🏆 Достижения", callback_data="achv"),
        InlineKeyboardButton(text="🤝 Репутация", callback_data="rep"),
        _gated_btn("🌳 Таланты", "talents", "talents", lvl),
        InlineKeyboardButton(text="📖 Бестиарий", callback_data="bestiary"),
        _gated_btn("🛠 Профессии", "profs", "professions", lvl),
        InlineKeyboardButton(text="🐾 Питомцы", callback_data="pets"),
        _gated_btn("🏅 Сезон", "season", "season", lvl),
        InlineKeyboardButton(text="🌐 События", callback_data="events"),
    ]
    items = [b for b in items if b]
    rows = [items[i:i + 2] for i in range(0, len(items), 2)]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="look")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_pets(ch: Character) -> InlineKeyboardMarkup:
    from engine import pets as _pets
    rows = []
    op = _pets.owned_pets(ch); ap = _pets.active_pet(ch)
    for pid in op:
        if pid != ap:
            cfg = _pets.PETS.get(pid, {})
            rows.append([InlineKeyboardButton(
                text=f"✅ Сделать активным: {cfg.get('emoji','🐾')} {cfg.get('name',pid)}",
                callback_data=f"petset:{pid}")])
    om = _pets.owned_mounts(ch); am = _pets.active_mount(ch)
    for mid in om:
        if mid != am:
            cfg = _pets.MOUNTS.get(mid, {})
            rows.append([InlineKeyboardButton(
                text=f"🐎 Оседлать: {cfg.get('name',mid)}", callback_data=f"mountset:{mid}")])
    for pid, cfg in _pets.PETS.items():
        if pid not in op:
            rows.append([InlineKeyboardButton(
                text=f"🛒 {cfg['emoji']} {cfg['name']} — 💰{money.fmt(cfg['cost'])}",
                callback_data=f"petbuy:{pid}")])
    for mid, cfg in _pets.MOUNTS.items():
        if mid not in om:
            rows.append([InlineKeyboardButton(
                text=f"🛒 {cfg['emoji']} {cfg['name']} — 💰{money.fmt(cfg['cost'])}",
                callback_data=f"mountbuy:{mid}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="stats")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_talents(ch: Character) -> InlineKeyboardMarkup:
    rows = []
    for tid, t in _talents.for_class(ch.cls).items():
        if _talents.points(ch) > 0 and _talents.rank(ch, tid) < t["max_rank"]:
            rows.append([InlineKeyboardButton(
                text=f"➕ {t['name']} ({_talents.rank(ch, tid)}/{t['max_rank']})",
                callback_data=f"talinv:{tid}")])
    rows.append([InlineKeyboardButton(text="♻️ Сбросить таланты", callback_data="talreset")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="stats")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_races() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"{r['emoji']} {r['name']}",
                                  callback_data=f"race:{rid}")]
            for rid, r in RACES.items()]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_classes(race: str = None) -> InlineKeyboardMarkup:
    allowed = RACES[race]["allowed_classes"] if race else list(CLASSES.keys())
    rows = []
    for cid in allowed:
        c = CLASSES[cid]
        badge = " ⭐" if c.get("newbie_ok") else ""
        rows.append([InlineKeyboardButton(text=f"{c['emoji']} {c['name']}{badge}",
                                          callback_data=f"pick:{cid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


DIR_ICONS = {"север": "⬆️", "юг": "⬇️", "восток": "➡️", "запад": "⬅️",
             "вверх": "🔼", "вниз": "🔽"}


def move_grid(exits, prefix: str = "go") -> list:
    """Кнопки перемещения пространственной сеткой:
            [Вверх]
      [Запад][Север][Восток]
            [Юг]
            [Вниз]
    Пустые стороны заполняются неактивной кнопкой, чтобы сетка не «съезжала».
    prefix — префикс callback ('go' для комнаты, 'mapgo' для карты)."""
    def b(d):
        return InlineKeyboardButton(text=f"{DIR_ICONS.get(d,'')}{d.capitalize()}",
                                    callback_data=f"{prefix}:{d}")
    blank = InlineKeyboardButton(text="·", callback_data="noop")
    rows = []
    if "вверх" in exits:
        rows.append([b("вверх")])
    if any(x in exits for x in ("запад", "север", "восток")):
        rows.append([b("запад") if "запад" in exits else blank,
                     b("север") if "север" in exits else blank,
                     b("восток") if "восток" in exits else blank])
    if "юг" in exits:
        rows.append([b("юг")])
    if "вниз" in exits:
        rows.append([b("вниз")])
    return rows


# ───────── сворачивание длинных списков комнаты (плейтест: меню разрослось) ─────────
def _collapse(items: list, limit: int, keep: int = None) -> tuple:
    """Чистый хелпер: если items больше limit — вернуть первые keep элементов
    (по умолчанию keep=limit), иначе вернуть все items как есть.
    Возвращает (visible, total): total == len(items) всегда; сравнение
    len(visible) < total говорит вызывающему коду, нужна ли кнопка «ещё».
    Без побочных эффектов и без зависимости от aiogram — удобно для юнит-тестов."""
    total = len(items)
    if total <= limit:
        return list(items), total
    keep = limit if keep is None else keep
    return list(items[:keep]), total


MOB_COLLAPSE_LIMIT = 4
MOB_COLLAPSE_KEEP = 3
CORPSE_COLLAPSE_LIMIT = 1
GROUND_COLLAPSE_LIMIT = 2
NPC_COLLAPSE_LIMIT = 2
NPC_COLLAPSE_KEEP = 2


def _mob_row(ch: Character, m: MobInstance) -> list:
    df = DIFF_EMOJI[_combat.mob_difficulty(ch.level, m.meta.get("level", 1))]
    return [InlineKeyboardButton(
        text=f"⚔️ {df} {m.meta['emoji']} {m.meta['name']} ур.{m.meta.get('level',1)}",
        callback_data=f"atk:{m.key}")]


def _npc_row(n: str) -> list:
    return [InlineKeyboardButton(
        text=f"💬 {npclib.display_name(n)} · {npclib.role_label(n)}",
        callback_data=f"talk:{n}")]


def kb_mobs_all(ch: Character, world: World) -> InlineKeyboardMarkup:
    """Экран-список ВСЕХ врагов комнаты (кнопка «⚔️ Все враги (N)» в kb_room)."""
    rows = [_mob_row(ch, m) for m in world.living_in(ch.room)]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="look")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_npcs_all(ch: Character, world: World) -> InlineKeyboardMarkup:
    """Экран-список ВСЕХ жителей комнаты (кнопка «💬 Все жители (N)» в kb_room)."""
    rows = [_npc_row(n) for n in WORLD[ch.room].get("npc", [])]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="look")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_more(ch: Character) -> InlineKeyboardMarkup:
    """Экран «☰ Ещё»: второстепенные системные пункты, вынесенные из главного
    меню комнаты, чтобы типовая комната укладывалась в ≤9 рядов.
    Группа/Сезон — гейтованные фичи (Этап 4.2, engine/uigate.py)."""
    lvl = ch.level
    row1 = [b for b in [
        _gated_btn("👥 Группа", "group", "party", lvl),
        InlineKeyboardButton(text="⚙ Настройки", callback_data="settings"),
    ] if b]
    row2 = [b for b in [
        InlineKeyboardButton(text="❓ Помощь", callback_data="help"),
        _gated_btn("🏅 Сезон", "season", "season", lvl),
    ] if b]
    rows = []
    if row1:
        rows.append(row1)
    if row2:
        rows.append(row2)
    rows.append([InlineKeyboardButton(text="📖 Бестиарий", callback_data="bestiary"),
                 InlineKeyboardButton(text="🏆 Достижения", callback_data="achv")])
    rows.append([InlineKeyboardButton(text="📜 Хроника", callback_data="chronicle")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="look")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_room(ch: Character, world: World) -> InlineKeyboardMarkup:
    rows = []
    # ── мобы: >4 -> первые 3 + «Все враги (N)» ──
    mobs = world.living_in(ch.room)
    visible_mobs, n_mobs = _collapse(mobs, MOB_COLLAPSE_LIMIT, MOB_COLLAPSE_KEEP)
    for m in visible_mobs:
        rows.append(_mob_row(ch, m))
    if len(visible_mobs) < n_mobs:
        rows.append([InlineKeyboardButton(text=f"⚔️ Все враги ({n_mobs})", callback_data="mobs")])
    # ── трупы: >1 -> «Обыскать тела (N)», обыскивает все разом ──
    corpses = [c for c in world.corpses_in(ch.room) if c.get("loot")]
    if len(corpses) > CORPSE_COLLAPSE_LIMIT:
        rows.append([InlineKeyboardButton(
            text=f"💀 Обыскать тела ({len(corpses)})", callback_data="loots")])
    else:
        for c in corpses:
            rows.append([InlineKeyboardButton(
                text=f"💰 Обыскать: {c['name']}", callback_data=f"loot:{c['key']}")])
    # направления пространственной сеткой (Вверх / Запад-Север-Восток / Юг / Вниз)
    rows.extend(move_grid(WORLD[ch.room]["exits"], prefix="go"))
    # ── земля: >2 -> «Подобрать всё (N)» ──
    ground = ground_items_for(ch, ch.room)
    if len(ground) > GROUND_COLLAPSE_LIMIT:
        rows.append([InlineKeyboardButton(
            text=f"✋ Подобрать всё ({len(ground)})", callback_data="takeall")])
    else:
        for it in ground:
            rows.append([InlineKeyboardButton(text=f"✋ {ITEMS[it]['name']}",
                                              callback_data=f"take:{it}")])
    # ── NPC: >2 -> первые 2 + «Все жители (N)» ──
    npcs = WORLD[ch.room].get("npc", [])
    visible_npcs, n_npcs = _collapse(npcs, NPC_COLLAPSE_LIMIT, NPC_COLLAPSE_KEEP)
    for n in visible_npcs:
        rows.append(_npc_row(n))
    if len(visible_npcs) < n_npcs:
        rows.append([InlineKeyboardButton(text=f"💬 Все жители ({n_npcs})", callback_data="npcs")])
    # действия комнаты: по 2 в ряд, короткие лейблы (аукцион/отдых/сундук/привязка)
    _action_btns = []
    # кнопка «Магазин(ы)» убрана: к торговцу обращаются нажатием на него (💬) выше
    # Аукцион — гейтованная фича (Этап 4.2, engine/uigate.py): заперта до 10 ур.
    if WORLD[ch.room].get("bank") or WORLD[ch.room].get("auction"):
        _auc_btn = _gated_btn("🏛 Аукцион", "auc", "auction", ch.level)
        if _auc_btn:
            _action_btns.append(_auc_btn)
    if WORLD[ch.room].get("rest"):
        _action_btns.append(InlineKeyboardButton(text="💤 Отдохнуть", callback_data="rest"))
    if WORLD[ch.room].get("personal"):
        _ns = len(ch.flags.get("stash") or [])
        _action_btns.append(InlineKeyboardButton(
            text=(f"📦 Сундук ({_ns})" if _ns else "📦 Сундук"), callback_data="stash"))
    if WORLD[ch.room].get("respawn"):
        bound = ch.flags.get("bind") == ch.room
        _action_btns.append(InlineKeyboardButton(
            text=("🪦 Привязано ✅" if bound else "🪦 Привязать"), callback_data="bind"))
    for _i in range(0, len(_action_btns), 2):
        rows.append(_action_btns[_i:_i + 2])
    # добыча/данж — как есть (полноразмерные лейблы, по одному в ряд)
    from engine import professions as _prof
    for _i, _node in enumerate(_prof.nodes_in(ch.room)):
        _iname = ITEMS.get(_node["item"], {}).get("name", _node["item"])
        _emoji = _prof.PROFS.get(_node["prof"], {}).get("emoji", "⛏")
        _left = _prof.cooldown_left(ch, ch.room, _i)
        _lab = (f"{_emoji} Истощён ⏳{_left}с" if _left > 0
                else f"{_emoji} Добыть: {_iname} (ур.{_node.get('skill_req',1)})")
        rows.append([InlineKeyboardButton(text=_lab, callback_data=f"gather:{_i}")])
    from engine import dungeon as _dgn
    _df = _dgn.find_by_entrance(ch.room)
    if _df:
        _did, _dcfg = _df
        _left = _dgn.cooldown_left(ch, _did)
        if _left > 0:
            _dlabel = f"🏰 {_dcfg['name']} ⏳{_left//60}м"
        else:
            _dlabel = f"🏰 Войти: {_dcfg['name']} (ур.{_dcfg.get('min_level',1)}+)"
        rows.append([InlineKeyboardButton(text=_dlabel, callback_data=f"dungeon:{_did}")])
    # системный ряд: Герой · Журнал · Карта · Ещё (остальное — под «Ещё»)
    rows.append([
        InlineKeyboardButton(text="📊 Герой", callback_data="stats"),
        InlineKeyboardButton(text="📖 Журнал", callback_data="journal"),
        InlineKeyboardButton(text="🗺 Карта", callback_data="map"),
        InlineKeyboardButton(text="☰ Ещё", callback_data="more"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_combat(ch: Character, world: World) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="🗡 Атаковать", callback_data=f"atk:{ch.target}")]]
    # Дух (RULES_V2): цель бесплотна — режущее/колющее резистится (см. rules2
    # _CAT_PROFILE["spirit"]). Пометим ✨ умения, чей тип урона ЭТО не задевает
    # (dmg_type НЕ pierce/slash), чтобы игрок видел контрплей прямо на кнопках.
    from engine import rules2 as _r2c
    _is_spirit = False
    if _r2c.ENABLED and ch.target:
        _tgt_mob = world.find(ch.room, ch.target)
        if _tgt_mob is not None:
            _is_spirit = _r2c.mob_profile(_tgt_mob.meta)["category"] == "spirit"
    # скиллы (с учётом кулдауна/маны — показываем статус)
    skill_row = []
    for sid in ch.skills:
        sk = SKILLS[sid]
        cd = ch.cooldowns.get(sid, 0)
        mark = "✨" if _is_spirit and _combat._skill_dtype(sk) not in ("pierce", "slash") else ""
        if cd > 0:
            label = f"{sk['emoji']}⏳{cd}"
        elif ch.mp < sk["mp"]:
            label = f"{sk['emoji']}{ch.resource_emoji}"
        else:
            label = f"{sk['emoji']} {sk['name']}"
        if mark:
            label = mark + label
        skill_row.append(InlineKeyboardButton(text=label, callback_data=f"skill:{sid}"))
        if len(skill_row) == 2:
            rows.append(skill_row); skill_row = []
    if skill_row:
        rows.append(skill_row)
    # расходники: по одной кнопке на лечилку и на зелье маны (чтобы не раздувать)
    heal_shown = mana_shown = False
    for it in dict.fromkeys(ch.inventory):
        meta = ITEMS[it]
        if meta["type"] != "consumable":
            continue
        eff = meta.get("effect", {})
        if "heal" in eff and not heal_shown:
            rows.append([InlineKeyboardButton(
                text=f"🧪 {meta['name']}", callback_data=f"use:{it}")])
            heal_shown = True
        elif "mana" in eff and not mana_shown and ch.resource_type == "mana":
            rows.append([InlineKeyboardButton(
                text=f"💙 {meta['name']}", callback_data=f"use:{it}")])
            mana_shown = True
    rows.append([InlineKeyboardButton(text="🏃 Бежать", callback_data="flee")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_inventory(ch: Character) -> InlineKeyboardMarkup:
    rows = []
    equipped = set(v for v in ch.equipment.values() if v)
    for it in dict.fromkeys(ch.inventory):
        meta = ITEMS[it]
        cnt = ch.inventory.count(it)
        emoji = meta.get("emoji", "•")
        tag = ""
        if it in equipped:
            tag = " ✅"
        elif cnt > 1:
            tag = f" x{cnt}"
        rows.append([InlineKeyboardButton(text=f"{emoji} {meta['name']}{tag}",
                                          callback_data=f"card:inv:{it}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="stats")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def render_stash(ch: Character) -> str:
    stash = ch.flags.get("stash") or []
    L = ["📦 *Личный сундук*", "",
         "_Надёжное хранилище — вещи здесь не теряются при смерти._", ""]
    if stash:
        L.append("*В сундуке:*")
        for it in dict.fromkeys(stash):
            cnt = stash.count(it)
            tag = f" x{cnt}" if cnt > 1 else ""
            L.append(f"  {ITEMS[it].get('emoji','•')} {ITEMS[it]['name']}{tag}")
    else:
        L.append("_Сундук пуст._")
    return "\n".join(L)


def kb_stash(ch: Character) -> InlineKeyboardMarkup:
    """Личное хранилище: достать из сундука / положить из сумки."""
    rows = []
    stash = ch.flags.get("stash") or []
    equipped = set(v for v in ch.equipment.values() if v)
    for it in dict.fromkeys(stash):
        cnt = stash.count(it)
        tag = f" x{cnt}" if cnt > 1 else ""
        rows.append([InlineKeyboardButton(
            text=f"⬇️ Достать: {ITEMS[it].get('emoji','•')} {ITEMS[it]['name']}{tag}",
            callback_data=f"stashget:{it}")])
    for it in dict.fromkeys(ch.inventory):
        if it in equipped:
            continue
        cnt = ch.inventory.count(it)
        tag = f" x{cnt}" if cnt > 1 else ""
        rows.append([InlineKeyboardButton(
            text=f"⬆️ Положить: {ITEMS[it].get('emoji','•')} {ITEMS[it]['name']}{tag}",
            callback_data=f"stashput:{it}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="look")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


SHOP_ITEMS = ["малое_зелье", "большое_зелье", "зелье_маны", "большое_зелье_маны",
              "эликсир", "ржавый_меч", "железный_меч", "стальной_меч",
              "дубовый_посох", "кинжалы_теней", "кожаный_доспех", "кольчуга",
              "латы", "мантия_мага", "амулет_силы"]

# uid -> выбранный торговец (когда в комнате несколько vendor-NPC)
active_vendor: dict = {}


def vendors_here(ch: Character) -> list:
    return [n for n in WORLD[ch.room].get("npc", [])
            if (npclib.get(n) or {}).get("role") == "vendor"]


def current_vendor(ch: Character):
    """NPC-торговец, чей прилавок сейчас открыт у игрока (выбранный или первый)."""
    vs = vendors_here(ch)
    vid = active_vendor.get(ch.uid)
    if vid in vs:
        return vid
    return vs[0] if vs else None


def shop_stock(ch: Character) -> list:
    """Ассортимент именно того торговца, к которому обратился игрок."""
    v = current_vendor(ch)
    if v:
        return SHOPS.get(v) or SHOPS.get("_default") or SHOP_ITEMS
    return SHOPS.get("_default") or SHOP_ITEMS


def vendor_sell_types(ch: Character) -> set:
    """Типы предметов, которые ПОКУПАЕТ текущий торговец (= что он сам продаёт)."""
    types = {ITEMS[k].get("type") for k in shop_stock(ch) if k in ITEMS}
    types.discard(None)
    return types


def kb_shop(ch: Character) -> InlineKeyboardMarkup:
    rows = []
    for key in shop_stock(ch):
        if key not in ITEMS:
            continue
        it = ITEMS[key]
        req = it.get("class_req")
        if req and ch.cls not in req:
            continue
        rows.append([InlineKeyboardButton(
            text=f"🛒 {it['name']} — 💰{money.fmt(it['price'])}", callback_data=f"card:shop:{key}")])
    bottom = [InlineKeyboardButton(text="💰 Продать добычу", callback_data="sellmenu")]
    if "кузнец" in WORLD[ch.room].get("npc", []):
        _craft_btn = _gated_btn("🔨 Ковка", "craftmenu", "craft", ch.level)
        if _craft_btn:
            bottom.append(_craft_btn)
        bottom.append(InlineKeyboardButton(text="🔧 Ремонт", callback_data="repairmenu"))
        bottom.append(InlineKeyboardButton(text="✨ Зачар", callback_data="enchmenu"))
        bottom.append(InlineKeyboardButton(text="💎 Сокеты", callback_data="socketmenu"))
    rows.append(bottom)
    # Лавка престижных титулов (золотосток эндгейма) — доступна у любого торговца.
    rows.append([InlineKeyboardButton(text="🎖 Титулы за золото", callback_data="titleshop")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="look")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_title_shop(ch) -> InlineKeyboardMarkup:
    """Лавка покупных титулов-косметики (сток золота). Кнопка на титул:
    купить (если не куплен и хватает денег) либо пометка «куплено»."""
    from engine import titles as _t
    rows = []
    for tid, name, price, owned in _t.for_shop(ch):
        if owned:
            rows.append([InlineKeyboardButton(text=f"✅ {name} — куплено",
                                              callback_data="noop")])
        else:
            afford = "💰" if ch.gold >= price else "🔒"
            rows.append([InlineKeyboardButton(
                text=f"{afford} {name} — {money.fmt(price)}",
                callback_data=f"buytitle:{tid}")])
    rows.append([InlineKeyboardButton(text="🎖 Мои титулы", callback_data="achv")])
    rows.append([InlineKeyboardButton(text="🏪 К покупкам", callback_data="shop")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="look")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def render_title_shop(ch) -> str:
    """Текст лавки титулов: список с ценой/описанием и текущим балансом."""
    from engine import titles as _t
    L = ["🎖 *Лавка престижных титулов*",
         "_Звания без боевого преимущества — только слава и сток золота._", ""]
    for tid, name, price, owned in _t.for_shop(ch):
        t = _t.TITLES[tid]
        tag = "✅ куплено" if owned else f"💰{money.fmt(price)}"
        L.append(f"• «{name}» — {tag}")
        L.append(f"  _{t['desc']}_")
    L.append("")
    L.append(f"Ваше золото: 💰{money.fmt(ch.gold)}")
    return "\n".join(L)


def kb_sell(ch: Character) -> InlineKeyboardMarkup:
    """Список предметов игрока, которые кузнец готов скупить."""
    rows = []
    counts = {}
    for it in ch.inventory:
        counts[it] = counts.get(it, 0) + 1
    equipped = set(v for v in ch.equipment.values() if v)
    allowed = vendor_sell_types(ch)
    for key, cnt in counts.items():
        price = content.sell_price(key)
        if price <= 0:
            continue
        meta = ITEMS[key]
        if allowed and meta.get("type") not in allowed:
            continue
        # не предлагаем продавать надетое
        avail = cnt - (1 if key in equipped else 0)
        if avail <= 0:
            continue
        cc = f" x{avail}" if avail > 1 else ""
        rows.append([InlineKeyboardButton(
            text=f"{meta.get('emoji','•')} {meta['name']}{cc} → 💰{money.fmt(price)}",
            callback_data=f"sell:{key}")])
    if not rows:
        rows.append([InlineKeyboardButton(text="(нечего продавать)", callback_data="noop")])
    rows.append([InlineKeyboardButton(text="🏪 К покупкам", callback_data="shop")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="look")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def render_craft(ch: Character) -> str:
    """Текстовый список рецептов с пометкой доступности."""
    from engine import craft as _craft
    L = ["🔨 *Кузница — ковка.* Принеси материалы и монеты:", ""]
    for rid in _craft.recipes_at("кузнец"):
        r = content.RECIPES[rid]
        ins = ", ".join(f"{ITEMS[k]['name']}×{q}" for k, q in r.get("inputs", []))
        gold = r.get("gold", 0)
        mark = "✅" if _craft.can_craft(ch, rid) else "▫️"
        L.append(f"{mark} *{r['name']}*\n   из: {ins}" + (f" + 💰{money.fmt(gold)}" if gold else ""))
    return "\n".join(L)


def kb_craft(ch: Character) -> InlineKeyboardMarkup:
    from engine import craft as _craft
    rows = []
    for rid in _craft.recipes_at("кузнец"):
        r = content.RECIPES[rid]
        ok = _craft.can_craft(ch, rid)
        prefix = "🔨" if ok else "🔒"
        cb = f"craft:{rid}" if ok else "noop"
        rows.append([InlineKeyboardButton(text=f"{prefix} {r['name']}", callback_data=cb)])
    rows.append([InlineKeyboardButton(text="🏪 К покупкам", callback_data="shop")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="look")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ───────────────────── ДИАЛОГ С NPC (КВЕСТЫ) ─────────────────────
from engine.content import QUESTS
from engine import quest as _quest
from engine import errands as _errands

def _npc_highlight_row(ch: Character, npc: str, action: dict):
    """Заметная кнопка-призыв под репликой NPC, если ИИ предложил действие.
    action уже валидирован (ai/actions.py): offer_quest / to_vendor / to_trainer."""
    if not action:
        return None
    name = action.get("action")
    if name == "offer_quest":
        qid = action.get("quest_id")
        if qid in QUESTS and qid in _quest.available_quests(ch, npc):
            return [InlineKeyboardButton(
                text=f"✨📜 Взять задание: {QUESTS[qid]['name']}",
                callback_data=f"qaccept:{qid}")]
    elif name == "offer_errand":
        # предложение уже собрано и сложено в ch.flags["errand_pending"] ботом;
        # кнопка сразу принимает его (LLM выбрал и озвучил).
        if ch.flags.get("errand_pending"):
            return [InlineKeyboardButton(text="✨✉️ Взять поручение",
                                         callback_data=f"erraccept:{npc}")]
    elif name == "to_vendor":
        return [InlineKeyboardButton(text="✨🛒 Показать товары",
                                     callback_data=f"shop:{npc}")]
    elif name == "to_trainer":
        return [InlineKeyboardButton(text="✨🎓 К обучению", callback_data="train")]
    return None


# роли NPC, которым уместно давать разовые поручения без ИИ (fallback-кнопка)
_ERRAND_ROLES = {"vendor", "trainer", "mentor", "guard", "questgiver"}


def _errand_rows(ch: Character, npc: str):
    """Строки-кнопки поручения под репликой NPC (сдача / взять-fallback)."""
    rows = []
    e = ch.flags.get("errand")
    if e and e.get("npc") == npc:
        # активное поручение у выдавшего NPC: доложить (если готово)
        if _errands.can_turn_in(ch, npc):
            rows.append([InlineKeyboardButton(text="✅ Доложить о поручении",
                                              callback_data=f"errturnin:{npc}")])
    elif (npclib.get(npc) or {}).get("role") in _ERRAND_ROLES:
        # нет активного и роль подходящая — всегда доступный fallback-путь
        if _errands.can_offer(ch, npc):
            rows.append([InlineKeyboardButton(text="✉️ Поручение",
                                              callback_data=f"erroffer:{npc}")])
    return rows


def kb_errand_offer(ch: Character, npc: str) -> InlineKeyboardMarkup:
    """Экран показанного предложения поручения: принять или вернуться."""
    rows = [[InlineKeyboardButton(text="✉️ Взять поручение",
                                  callback_data=f"erraccept:{npc}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"talk:{npc}")]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_journal(ch: Character) -> InlineKeyboardMarkup:
    """Клавиатура журнала: бросить активное поручение (если есть) + назад."""
    rows = []
    if ch.flags.get("errand"):
        rows.append([InlineKeyboardButton(text="✖️ Бросить поручение",
                                          callback_data="errabandon")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="look")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_npc(ch: Character, npc: str, highlight: dict = None) -> InlineKeyboardMarkup:
    rows = []
    hi = _npc_highlight_row(ch, npc, highlight)
    if hi:
        rows.append(hi)
    # поручения (сдача / fallback-взятие без ИИ)
    rows.extend(_errand_rows(ch, npc))
    # доступные для получения
    for qid in _quest.available_quests(ch, npc):
        rows.append([InlineKeyboardButton(
            text=f"📜 Взять: {QUESTS[qid]['name']}", callback_data=f"qaccept:{qid}")])
    # готовые к сдаче
    for qid in _quest.turn_in_quests(ch, npc):
        rows.append([InlineKeyboardButton(
            text=f"✅ Сдать: {QUESTS[qid]['name']}", callback_data=f"qdone:{qid}")])
    # choose-квесты: кнопки вариантов выбора (подтверждение — двухшаговое, в боте)
    for qid, opts in _quest.pending_choices(ch, npc):
        for o in opts:
            rows.append([InlineKeyboardButton(
                text=f"🔀 {o.get('label', o.get('id'))}",
                callback_data=f"choice:{qid}:{o.get('id')}")])
    # особое: жрец снимает PvP-метку за деньги (искупление)
    if npc == "жрец_храма":
        from engine import karma as _km
        if _km.pvp_marked(ch):
            rows.append([InlineKeyboardButton(
                text=f"🕊 Искупить PvP-метку (💰{money.fmt(_km.CLEAR_COST)})",
                callback_data="pkclear")])
    # особое: жрец храма даёт святую воду
    if npc == "жрец_храма" and "святая_вода" not in ch.inventory:
        rows.append([InlineKeyboardButton(text="💧 Взять святую воду",
                                          callback_data="holy_water")])
    # учитель: обучение умениям своего класса
    if (npclib.get(npc) or {}).get("role") == "trainer":
        rows.append([InlineKeyboardButton(text="🎓 Обучение умениям",
                                          callback_data="train")])
    if (npclib.get(npc) or {}).get("role") == "mentor":
        _daily_btn = _gated_btn("📅 Задание дня", "daily", "daily", ch.level)
        if _daily_btn:
            rows.append([_daily_btn])
        from engine.character import LEVEL_CAP
        if ch.level >= LEVEL_CAP:
            rows.append([InlineKeyboardButton(
                text=f"🌟 Реморт (перерождение, +{int(ch.REMORT_BONUS_PER*100)}% силы)",
                callback_data="remort")])
    if (npclib.get(npc) or {}).get("role") == "vendor":
        rows.append([InlineKeyboardButton(text="🛒 Купить / Продать", callback_data=f"shop:{npc}")])
    if (npclib.get(npc) or {}).get("role") == "arena_master":
        _arena_btn = _gated_btn("🏟 Таблица арены", "arenaboard", "arena", ch.level)
        if _arena_btn:
            rows.append([_arena_btn])
    _tp = (npclib.get(npc) or {}).get("teaches_prof")
    if _tp:
        from engine import professions as _pf
        _pm = _pf.PROFS.get(_tp, {})
        if _pf.is_learned(ch, _tp):
            rows.append([InlineKeyboardButton(
                text=f"✅ {_pm.get('name', _tp)} освоено", callback_data="noop")])
        else:
            rows.append([InlineKeyboardButton(
                text=f"📚 Освоить «{_pm.get('name', _tp)}»", callback_data=f"learnprof:{_tp}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="look")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ───────────────────── УМЕНИЯ / ЛОУДАУТ / УЧИТЕЛЬ ─────────────────────
def render_skills(ch: Character) -> str:
    L = [f"📜 *Умения — {CLASSES[ch.cls]['name']}* (в панели {len(ch.loadout)}/{ch.LOADOUT_MAX})", ""]
    for sid in skillmod.all_class_skills(ch.cls):
        sk = SKILLS[sid]
        st = skillmod.status(ch, sid)
        if st == "learned":
            mark = "🟢" if sid in ch.loadout else "⚪"
            tag = " (в панели)" if sid in ch.loadout else ""
        elif st == "learnable":
            mark = "🟡"; tag = f" — можно выучить (💰{money.fmt(skillmod.learn_cost(sid))})"
        else:
            mark = "🔒"; tag = f" — с ур.{skillmod.learn_level(sid)}"
        aoe = " 🌀" if sk.get("aoe") else ""
        cost = f" {ch.resource_emoji}{sk['mp']}" if sk.get("mp") else ""
        L.append(f"{mark} {sk['emoji']} *{sk['name']}*{aoe}{cost}{tag}\n   _{sk.get('desc','')}_")
    L.append("\n🟢 в панели · ⚪ выучено · 🟡 доступно · 🔒 закрыто")
    return "\n".join(L)


def kb_skills(ch: Character) -> InlineKeyboardMarkup:
    """Переключение выученных умений в боевую панель (до 5)."""
    rows = []
    for sid in skillmod.all_class_skills(ch.cls):
        if sid not in ch.learned:
            continue
        sk = SKILLS[sid]
        in_load = sid in ch.loadout
        label = ("🟢 Убрать: " if in_load else "⚪ В панель: ") + sk['name']
        rows.append([InlineKeyboardButton(text=label, callback_data=f"slot:{sid}")])
    presets = ch.flags.get("presets") or {}
    rows.append([InlineKeyboardButton(text=f"💾 Слот {i}", callback_data=f"psave:{i}") for i in (1, 2, 3)])
    load_row = [InlineKeyboardButton(text=f"📥 Слот {i}", callback_data=f"pload:{i}")
                for i in (1, 2, 3) if str(i) in presets]
    if load_row:
        rows.append(load_row)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="stats")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def render_trainer(ch: Character) -> str:
    L = ["🎓 *Учитель* готов обучить умениям твоего класса.", ""]
    learnable = skillmod.learnable_now(ch)
    locked = skillmod.locked(ch)
    if learnable:
        L.append("*Доступно сейчас:*")
        for sid in learnable:
            sk = SKILLS[sid]
            L.append(f"• {sk['emoji']} *{sk['name']}* — 💰{money.fmt(skillmod.learn_cost(sid))}  _{sk.get('desc','')}_")
    else:
        L.append("_Сейчас нечего изучать — возвращайся на новых уровнях._")
    if locked:
        L.append("\n*Позже (по уровню):*")
        for sid in locked[:6]:
            L.append(f"🔒 {SKILLS[sid]['name']} — с ур.{skillmod.learn_level(sid)}")
    return "\n".join(L)


def kb_trainer(ch: Character) -> InlineKeyboardMarkup:
    rows = []
    for sid in skillmod.learnable_now(ch):
        sk = SKILLS[sid]
        rows.append([InlineKeyboardButton(
            text=f"📖 Выучить: {sk['name']} (💰{money.fmt(skillmod.learn_cost(sid))})",
            callback_data=f"learn:{sid}")])
    rows.append([InlineKeyboardButton(text="📜 Мои умения", callback_data="skills")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="look")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ───────────────────── НАСТРОЙКИ ─────────────────────
def _pk_status(ch) -> str:
    from engine import karma as _km
    return f"🔴 активна ({_km.remaining_human(ch)})" if _km.pvp_marked(ch) else "⚪ нет"


def render_settings(ch: Character) -> str:
    al = "включён ✅" if ch.flags.get("autoloot", False) else "выключен ❌"
    rp = "включены ✅" if ch.flags.get("roompics", True) else "выключены ❌"
    return ("⚙ *Настройки*\n\n"
            f"🎁 Авто-лут: *{al}*\n"
            "_Выключен — трупы обыскиваешь вручную; включён — добыча сразу в сумку._\n\n"
            f"🖼 Картинки локаций: *{rp}*\n"
            "_Включены — при входе в комнату показывается её изображение._\n\n"
            f"⚔️ PvP-метка: *{_pk_status(ch)}*\n"
            "_Метка выдаётся автоматически за убийство игрока вне безопасных зон, "
            "держится несколько дней и спадает сама — либо её снимет жрец за плату._")


def kb_settings(ch: Character) -> InlineKeyboardMarkup:
    al = ch.flags.get("autoloot", False)
    rp = ch.flags.get("roompics", True)
    rows = [[InlineKeyboardButton(
        text=("🎁 Авто-лут: ВКЛ — выключить" if al else "🎁 Авто-лут: ВЫКЛ — включить"),
        callback_data="set:autoloot")]]
    rows.append([InlineKeyboardButton(
        text=("🖼 Картинки локаций: ВКЛ — выключить" if rp else "🖼 Картинки локаций: ВЫКЛ — включить"),
        callback_data="set:roompics")])
    from engine import notify as _nf
    if _nf.ENABLED:
        rows.append([InlineKeyboardButton(text="🔔 Уведомления", callback_data="notify")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="look")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ───────── уведомления (push) ─────────
def render_notify(ch: Character) -> str:
    from engine import notify as _nf
    lim = _nf.limit(ch)
    quiet_line = ("_Тихие часы выключены вами — push придут в любое время._"
                  if _nf.quiet_off(ch) else
                  "_Тихие часы 23:00–09:00: часть уведомлений откладывается до утра._")
    L = ["🔔 *Уведомления*", "",
         "_Бот пишет первым о важных событиях. Настройте, что получать._",
         f"_Не более {lim} в сутки (сделки аукциона — вне лимита)._",
         quiet_line, ""]
    for cat in _nf.CATEGORIES:
        on = "включено ✅" if _nf.enabled(ch, cat) else "выключено ❌"
        L.append(f"{_nf.LABELS.get(cat, cat)}: *{on}*")
    return "\n".join(L)


def kb_notify(ch: Character) -> InlineKeyboardMarkup:
    from engine import notify as _nf
    rows = []
    for cat in _nf.CATEGORIES:
        on = _nf.enabled(ch, cat)
        mark = "🔔" if on else "🔕"
        rows.append([InlineKeyboardButton(
            text=f"{mark} {_nf.LABELS.get(cat, cat)}",
            callback_data=f"ntog:{cat}")])
    rows.append([InlineKeyboardButton(
        text=f"🔔 Лимит: {_nf.limit(ch)}/день", callback_data="nlim")])
    quiet_label = "🌙 Тихие часы: выкл" if _nf.quiet_off(ch) else "🌙 Тихие часы: вкл"
    rows.append([InlineKeyboardButton(text=quiet_label, callback_data="nquiet")])
    _off = _nf.tz_offset(ch)
    rows.append([InlineKeyboardButton(
        text=f"🕐 Часовой пояс: UTC{_off:+d}", callback_data="ntz")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="settings")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ───────── выбор часового пояса (Этап 7.2) ─────────
def render_tz(ch: Character) -> str:
    from engine import notify as _nf
    off = _nf.tz_offset(ch)
    return ("🕐 *Часовой пояс*\n\n"
            f"Текущий: *UTC{off:+d}* (по умолчанию +3, МСК).\n\n"
            "_Тихие часы 23:00–09:00 считаются по вашему местному времени — "
            "выберите смещение от UTC, чтобы ночные push не будили вас._")


def kb_tz(ch: Character) -> InlineKeyboardMarkup:
    from engine import notify as _nf
    cur = _nf.tz_offset(ch)
    rows = []
    # кнопки смещений −2..+12 рядами по 5; текущий помечаем галочкой
    row = []
    for off in range(_nf.TZ_MIN, _nf.TZ_MAX + 1):
        mark = "✅" if off == cur else ""
        row.append(InlineKeyboardButton(text=f"{mark}{off:+d}", callback_data=f"ntzset:{off}"))
        if len(row) == 5:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="notify")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ───────── карточка предмета (фото + статы + действие) ─────────
_TYPE_RU = {"consumable": "Расходник", "weapon": "Оружие", "armor": "Броня",
            "accessory": "Аксессуар", "material": "Материал", "quest": "Квестовый предмет"}
_STAT_RU = {"atk": "Атака", "defense": "Защита", "str": "Сила", "dex": "Ловкость",
            "int": "Интеллект", "spi": "Дух", "hp": "HP", "mp": "MP",
            "crit": "Крит%", "dodge": "Уклонение"}


def item_caption(key: str, ctx: str, ch: Character) -> str:
    from engine import rarity as _rar, equip as _eq
    meta = ITEMS[key]
    L = [f"{meta.get('emoji','•')} *{meta['name']}*",
         f"_{_TYPE_RU.get(meta.get('type',''), meta.get('type',''))}_"]
    _r = _rar.rarity_of(key)
    if _r != "common":
        L.append(f"{_rar.META[_r]['emoji']} _{_rar.META[_r]['name']}_")
    if meta.get("type") in ("weapon", "armor") or meta.get("slot"):
        _lr = _eq.level_req(meta)
        ok, why = _eq.can_equip(ch, key)
        L.append(f"📈 Требуется уровень: {_lr}" + ("" if ok else f"  ⛔ _{why}_"))
        _rr = int(meta.get("remort_req", 0) or 0)
        if _rr:
            L.append(f"⭐ Реморт {_rr}")
    b = meta.get("bonus", {})
    if b:
        L.append("📊 " + ", ".join(
            f"{_STAT_RU.get(k, k)} {'+' if v >= 0 else ''}{v}" for k, v in b.items()))
    _afx = meta.get("affixes")
    if _afx:
        L.append("✨ Аффиксы: " + ", ".join(f"+{a} {n}" for n, a in _afx))
    e = meta.get("effect", {})
    if e:
        parts = []
        if "heal" in e:
            parts.append(f"+{e['heal']} HP")
        if "mana" in e:
            parts.append(f"+{e['mana']} MP")
        if parts:
            L.append("✨ " + ", ".join(parts))
    if meta.get("desc"):
        L.append("")
        L.append(meta["desc"])
    if ctx == "shop":
        L.append(f"\n💰 Цена: {money.fmt(meta.get('price', 0))}   (у вас: 💰{money.fmt(ch.gold)})")
    else:
        owned = ch.inventory.count(key)
        L.append(f"\n🎒 В сумке: {owned}")
        if key in [v for v in ch.equipment.values() if v]:
            L.append("✅ Надето")
    return "\n".join(L)


def kb_item_card(key: str, ctx: str, ch: Character) -> InlineKeyboardMarkup:
    meta = ITEMS[key]
    rows = []
    if ctx == "shop":
        req = meta.get("class_req")
        if req and ch.cls not in req:
            rows.append([InlineKeyboardButton(text="🔒 Не для вашего класса", callback_data="noop")])
        else:
            rows.append([InlineKeyboardButton(
                text=f"🛒 Купить — 💰{money.fmt(meta.get('price', 0))}", callback_data=f"cbuy:{key}")])
    else:
        t = meta.get("type")
        if t == "consumable":
            rows.append([InlineKeyboardButton(text="🧪 Использовать", callback_data=f"cuse:{key}")])
        elif t in ("weapon", "armor", "accessory"):
            req = meta.get("class_req")
            if req and ch.cls not in req:
                rows.append([InlineKeyboardButton(text="🔒 Не для вашего класса", callback_data="noop")])
            elif key in [v for v in ch.equipment.values() if v]:
                rows.append([InlineKeyboardButton(text="🚫 Снять", callback_data=f"cunequip:{key}")])
            else:
                rows.append([InlineKeyboardButton(text="⚙️ Надеть", callback_data=f"cequip:{key}")])
                # разборка в туманную пыль (утилизация в поле, не у кузнеца)
                from engine import salvage as _salv
                _d = _salv.dust_for(key)
                rows.append([InlineKeyboardButton(
                    text=f"🔨 Разобрать ({_d} пыли)", callback_data=f"salv:{key}")])
    rows.append([InlineKeyboardButton(text="⬅️ Закрыть", callback_data="cardx")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
