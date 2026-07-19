# -*- coding: utf-8 -*-
"""Алиасы направлений/действий, англ. короткие имена предметов, резолв целей.
Новый самодостаточный модуль (зависит только от engine), чтобы быть надёжно
тестируемым независимо от состояния остальных бот-файлов.
"""
from engine.content import ITEMS, SKILLS, WORLD
from engine.world import ground_items_for

# ── направления ──
DIR_RU2EN = {"север": "north", "юг": "south", "восток": "east",
             "запад": "west", "вверх": "up", "вниз": "down"}
DIR_ALIASES = {}
for _ru, _en in DIR_RU2EN.items():
    DIR_ALIASES[_ru] = _ru
    DIR_ALIASES[_en] = _ru
    DIR_ALIASES[_en[0]] = _ru          # n,s,e,w,u,d
DIR_ALIASES.update({"с": "север", "ю": "юг", "в": "восток", "з": "запад"})

# ── действия: алиас -> каноничный глагол ──
VERB_ALIASES = {
    "kill": "kill", "k": "kill", "a": "kill", "атака": "kill", "бей": "kill", "ударить": "kill",
    "cast": "cast", "c": "cast", "каст": "cast", "колдовать": "cast",
    "bash": "bash", "b": "bash", "подножка": "bash", "оглушить": "bash",
    "flee": "flee", "f": "flee", "сбежать": "flee", "бежать": "flee",
    "get": "get", "g": "get", "взять": "get", "поднять": "get",
    "drop": "drop", "бросить": "drop",
    "wield": "wield", "вооружиться": "wield",
    "use": "use", "исп": "use", "выпить": "use",
    "look": "look", "l": "look", "осмотр": "look",
    "score": "score", "sc": "score", "герой": "score", "статы": "score",
    "inv": "inv", "i": "inv", "сумка": "inv",
}

# ── англ. короткие имена предметов ──
_ITEM_ROOT_EN = [
    ("меч", "sword"), ("клинок", "sword"), ("сабл", "saber"), ("секира", "axe"),
    ("топор", "axe"), ("кинжал", "dagger"), ("нож", "knife"), ("копь", "spear"),
    ("пик", "pike"), ("булав", "mace"), ("молот", "hammer"), ("дубин", "club"),
    ("посох", "staff"), ("жезл", "wand"), ("щит", "shield"), ("лук", "bow"),
    ("брон", "armor"), ("доспех", "armor"), ("латы", "plate"), ("кольчуг", "mail"),
    ("роб", "robe"), ("мантия", "robe"), ("шлем", "helm"), ("капюшон", "hood"),
    ("сапог", "boots"), ("ботин", "boots"), ("перчат", "gloves"), ("наруч", "bracer"),
    ("плащ", "cloak"), ("пояс", "belt"), ("кольц", "ring"), ("амулет", "amulet"),
    ("ожерель", "necklace"), ("зель", "potion"), ("эликсир", "elixir"),
    ("свит", "scroll"), ("факел", "torch"), ("ключ", "key"), ("руд", "ore"),
    ("шкур", "hide"), ("трав", "herb"), ("кристалл", "crystal"), ("самоцвет", "gem"),
    ("камень", "stone"), ("хлеб", "bread"), ("мяс", "meat"), ("рыб", "fish"),
]
_ITEM_ALIAS_OVERRIDES = {}


def item_alias(key: str) -> str:
    if key in _ITEM_ALIAS_OVERRIDES:
        return _ITEM_ALIAS_OVERRIDES[key]
    meta = ITEMS.get(key, {})
    name = (meta.get("name") or key).lower()
    for root, en in _ITEM_ROOT_EN:
        if root in name or root in key.lower():
            return en
    return {"weapon": "weapon", "armor": "armor", "accessory": "trinket",
            "consumable": "potion", "material": "item", "quest": "item"}.get(
            meta.get("type"), "item")


def item_label(key: str) -> str:
    nm = ITEMS.get(key, {}).get("name", key)
    return f"{nm} ({item_alias(key)})"


def match_item(query: str, keys) -> str:
    q = (query or "").strip().lower()
    if not q:
        return None
    for k in keys:
        if item_alias(k).lower() == q:
            return k
    for k in keys:
        nm = ITEMS.get(k, {}).get("name", k).lower()
        if q in nm or q in item_alias(k).lower() or q in k.lower():
            return k
    return None


def match_skill(query: str, skill_ids) -> str:
    """Резолв заклинания/умения по англ. id или части русского имени."""
    q = (query or "").strip().lower()
    if not q:
        return None
    for sid in skill_ids:
        if sid.lower() == q:
            return sid
    for sid in skill_ids:
        if q in sid.lower() or q in SKILLS.get(sid, {}).get("name", "").lower():
            return sid
    return None


def resolve(text: str, ch, world):
    """Разобрать текстовую команду -> (verb, payload) или None.
    Не выполняет действие, только определяет намерение для бота.
    """
    parts = (text or "").strip().lower().lstrip("/").split()
    if not parts:
        return None
    word, arg = parts[0], " ".join(parts[1:])
    if word in DIR_ALIASES:
        d = DIR_ALIASES[word]
        return ("move", d) if d in WORLD[ch.room]["exits"] else ("nomove", d)
    verb = VERB_ALIASES.get(word)
    if not verb:
        return None
    if verb in ("look", "score", "inv", "flee", "bash"):
        return (verb, None)
    if verb == "cast":
        return ("cast", match_skill(arg, ch.skills))
    if verb == "kill":
        mob = next((m for m in world.living_in(ch.room)
                    if not arg or arg in m.meta["name"].lower()
                    or arg in m.mob_id.lower()), None)
        return ("kill", mob.key if mob else None)
    if verb == "get":
        return ("get", match_item(arg, ground_items_for(ch, ch.room)))
    if verb in ("use", "wield", "drop"):
        return (verb, match_item(arg, ch.inventory))
    return None
