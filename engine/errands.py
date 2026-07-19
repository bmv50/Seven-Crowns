# -*- coding: utf-8 -*-
"""
ИИ-поручения: динамические одноразовые задачи, которые NPC выдаёт сверх
статичного каталога квестов (engine/quest.py). Принцип фазы 3:

    ДВИЖОК генерирует ВАЛИДНЫЕ кандидаты, LLM только ВЫБИРАЕТ и ОЗВУЧИВАЕТ.
    Без ИИ — случайный кандидат из тех же валидных + шаблонный текст.

Кандидаты строятся из мобов уровня игрока (окно [level-2 .. level+3]) в зоне
NPC и соседних зонах: тип kill (убить N) и collect (собрать предметы из
loot-таблиц тех же мобов). Награда считается ФОРМУЛОЙ (не моделью) — см.
_reward(): срезана относительно статичного квеста, т.к. поручение повторяемо.

Состояние в ch.flags:
  ch.flags["errand"]        — одно активное поручение (см. accept());
  ch.flags["errand_pending"]— черновик предложения до принятия (транзиент);
  ch.flags["errand_day"]    — счётчик взятых за день {"date": iso, "n": int}.

Одно активное поручение на игрока; лимит MAX_PER_DAY взятых в день. Сдавать
можно только выдавшему NPC. Модуль без Telegram и без БД (движковый слой).
"""
import random
from datetime import date
from typing import List, Optional

from .content import WORLD, MOBS, ITEMS

# ── Баланс ──
LEVEL_LOW = 2            # окно уровней мобов: [level-LEVEL_LOW .. level+LEVEL_HIGH]
LEVEL_HIGH = 3
MAX_CANDIDATES = 8       # не показывать модели/игроку больше кандидатов
MAX_PER_DAY = 3          # лимит взятых поручений в день
MIN_LOOT_CHANCE = 0.2    # для collect: предмет должен падать не реже, чем с этим шансом

# Награда формулой (НЕ LLM). Базовая «ценность» цели — собственные xp/gold моба.
# Статичный kill-квест на N мобов даёт по замерам quests.yaml порядка
# 0.5·N·xp_моба опыта и ~0.6·N·gold_моба золота (сверх того, что падает с самих
# убийств). Поручение ПОВТОРЯЕМО (кап MAX_PER_DAY/день), поэтому берём срезанные
# множители — итог ~60-70% от сопоставимого одноразового статичного квеста.
ERRAND_XP_MULT = 0.35
ERRAND_GOLD_MULT = 0.40
BONUS_ITEM_CHANCE = 0.10    # редкий расходник в награду

# Расходник-бонус по уровню игрока (дешёвый лечебный — утилитарная приятность).
_CONSUMABLE_BY_LEVEL = [
    (12, "малое_зелье"),
    (30, "большое_зелье"),
    (10**9, "эликсир"),
]


def _today() -> str:
    return date.today().isoformat()


# ───────────────────────── ЗОНЫ И МОБЫ ─────────────────────────
def _npc_room(npc_id: str) -> Optional[str]:
    """Комната, в которой стоит NPC (первая найденная). None — если нигде."""
    for rid, room in WORLD.items():
        if npc_id in (room.get("npc") or []):
            return rid
    return None


def _zone_set(npc_id: str) -> set:
    """Зона NPC (= зона его комнаты) + соседние зоны (через выходы комнат)."""
    room = _npc_room(npc_id)
    if not room:
        return set()
    home = WORLD[room].get("zone")
    zones = {home}
    # соседние зоны: куда ведут выходы из комнат родной зоны
    for rid, r in WORLD.items():
        if r.get("zone") != home:
            continue
        for dest in (r.get("exits") or {}).values():
            dz = (WORLD.get(dest) or {}).get("zone")
            if dz:
                zones.add(dz)
    zones.discard(None)
    return zones


def _mobs_in_zones(zones: set) -> List[str]:
    """Уникальные id мобов, спавнящихся в комнатах указанных зон (детерминир.)."""
    seen = set()
    for rid, r in WORLD.items():
        if r.get("zone") not in zones:
            continue
        for mid in (r.get("spawns") or []):
            seen.add(mid)
    return sorted(seen)


def _is_boss(mid: str) -> bool:
    return bool((MOBS.get(mid) or {}).get("boss"))


# ───────────────────────── КАНДИДАТЫ ─────────────────────────
def _kill_count(mob_level: int, player_level: int) -> int:
    """count для kill (4..8): слабее моб — больше цель, сильнее — меньше."""
    d = mob_level - player_level
    return max(4, min(8, 6 - d))


def _collect_count(mob_level: int, player_level: int) -> int:
    """count для collect (3..6): аналогично kill, но мельче."""
    d = mob_level - player_level
    return max(3, min(6, 5 - d))


def candidates(ch, npc_id: str) -> List[dict]:
    """
    ВАЛИДНЫЕ кандидаты-поручения для (игрок, NPC). Детерминированный порядок,
    не более MAX_CANDIDATES. Каждый: {"type","mob"|"item"[,"from_mob"],
    "mob_level","count"}. Награда здесь НЕ считается (её ставит offer()).
    """
    lvl = int(getattr(ch, "level", 1))
    lo, hi = lvl - LEVEL_LOW, lvl + LEVEL_HIGH
    zones = _zone_set(npc_id)
    if not zones:
        return []

    eligible = []
    for mid in _mobs_in_zones(zones):
        m = MOBS.get(mid)
        if not m or _is_boss(mid):
            continue
        ml = int(m.get("level", 1))
        if lo <= ml <= hi:
            eligible.append((ml, mid))
    eligible.sort(key=lambda x: (x[0], x[1]))

    kills, collects, seen_items = [], [], set()
    for ml, mid in eligible:
        kills.append({"type": "kill", "mob": mid, "mob_level": ml,
                      "count": _kill_count(ml, lvl)})
        for entry in (MOBS[mid].get("loot") or []):
            if not entry or len(entry) < 2:
                continue
            item, chance = entry[0], entry[1]
            if chance < MIN_LOOT_CHANCE or item in seen_items:
                continue
            if (ITEMS.get(item) or {}).get("type") == "quest":
                continue           # квест-токены не годятся для сбора-поручения
            seen_items.add(item)
            collects.append({"type": "collect", "item": item, "from_mob": mid,
                             "mob_level": ml, "count": _collect_count(ml, lvl)})

    # чередуем kill/collect для разнообразия, детерминированно, обрезаем до лимита
    out = []
    for k, c in zip(kills, collects):
        out.append(k); out.append(c)
    tail = kills[len(collects):] if len(kills) > len(collects) else collects[len(kills):]
    out.extend(tail)
    return out[:MAX_CANDIDATES]


# ───────────────────────── НАГРАДА ─────────────────────────
def _consumable_for(level: int) -> str:
    for cap, item in _CONSUMABLE_BY_LEVEL:
        if level <= cap:
            return item
    return _CONSUMABLE_BY_LEVEL[-1][1]


def _reward(ch, cand: dict) -> dict:
    """Награда формулой от собственных xp/gold целевого моба и count."""
    mid = cand.get("mob") or cand.get("from_mob")
    m = MOBS.get(mid) or {}
    n = int(cand["count"])
    xp = max(1, round(n * int(m.get("xp", 0)) * ERRAND_XP_MULT))
    gold = max(1, round(n * int(m.get("gold", 0)) * ERRAND_GOLD_MULT))
    rew = {"xp": xp, "gold": gold, "items": []}
    if random.random() < BONUS_ITEM_CHANCE:
        rew["items"].append(_consumable_for(int(getattr(ch, "level", 1))))
    return rew


# ───────────────────────── ТЕКСТ ─────────────────────────
def _target_name(cand: dict) -> str:
    if cand["type"] == "kill":
        return (MOBS.get(cand["mob"]) or {}).get("name", cand["mob"])
    return (ITEMS.get(cand["item"]) or {}).get("name", cand["item"])


def _template_text(cand: dict, npc_id: str) -> str:
    """Шаблонная озвучка (fallback без ИИ)."""
    name = _target_name(cand)
    n = cand["count"]
    if cand["type"] == "kill":
        return f"Есть работёнка: проредить {name} — штук {n}. Возьмёшься?"
    return f"Мне бы {n} ед. «{name}». Принесёшь — не обижу."


# ───────────────────────── СОСТОЯНИЕ ─────────────────────────
def has_active(ch) -> bool:
    return bool(ch.flags.get("errand"))


def taken_today(ch) -> int:
    d = ch.flags.get("errand_day") or {}
    return int(d.get("n", 0)) if d.get("date") == _today() else 0


def _bump_taken(ch):
    n = taken_today(ch)
    ch.flags["errand_day"] = {"date": _today(), "n": n + 1}


def can_offer(ch, npc_id: str) -> bool:
    """Можно ли этому NPC сейчас предложить поручение (для кнопки/подсказки ИИ)."""
    if has_active(ch) or taken_today(ch) >= MAX_PER_DAY:
        return False
    return bool(candidates(ch, npc_id))


# ───────────────────────── API ─────────────────────────
def offer(ch, npc_id: str, choice: Optional[dict] = None) -> Optional[dict]:
    """
    Построить КОНКРЕТНОЕ предложение поручения (ещё не принятое).
    choice — валидированный выбор LLM {"idx": int, "text": str} либо None
    (тогда движок берёт случайного кандидата + шаблонный текст).
    Возвращает dict-предложение или None (нет кандидатов / плохой idx).
    """
    cands = candidates(ch, npc_id)
    if not cands:
        return None
    if choice is not None:
        idx = choice.get("idx")
        if not isinstance(idx, int) or not (0 <= idx < len(cands)):
            return None
        cand = cands[idx]
        text = (choice.get("text") or "").strip() or _template_text(cand, npc_id)
    else:
        cand = random.choice(cands)
        text = _template_text(cand, npc_id)

    off = {"npc": npc_id, "type": cand["type"], "count": int(cand["count"]),
           "reward": _reward(ch, cand), "text": text}
    if cand["type"] == "kill":
        off["mob"] = cand["mob"]
    else:
        off["item"] = cand["item"]
        off["from_mob"] = cand.get("from_mob")
    return off


def accept(ch, offer_dict: Optional[dict]) -> str:
    """Принять предложение: закрепить активное поручение, учесть дневной лимит."""
    if not offer_dict:
        return "Поручение недоступно."
    if has_active(ch):
        return "У тебя уже есть активное поручение — сначала заверши или брось его."
    if taken_today(ch) >= MAX_PER_DAY:
        return f"На сегодня хватит поручений (взято {MAX_PER_DAY}). Возвращайся завтра."
    e = {"npc": offer_dict["npc"], "type": offer_dict["type"],
         "count": int(offer_dict["count"]), "progress": 0,
         "reward": offer_dict.get("reward", {}), "text": offer_dict.get("text", ""),
         "day": _today()}
    if offer_dict["type"] == "kill":
        e["mob"] = offer_dict["mob"]
    else:
        e["item"] = offer_dict["item"]
        e["from_mob"] = offer_dict.get("from_mob")
    ch.flags["errand"] = e
    ch.flags.pop("errand_pending", None)
    _bump_taken(ch)
    return "✉️ Принято поручение!\n" + _goal_line(e)


def on_kill(ch, mob_id: str) -> Optional[str]:
    """Прогресс kill-поручения при убийстве моба. Строка-уведомление или None."""
    e = ch.flags.get("errand")
    if not e or e.get("type") != "kill" or e.get("mob") != mob_id:
        return None
    if e["progress"] >= e["count"]:
        return None
    e["progress"] += 1
    name = (MOBS.get(mob_id) or {}).get("name", mob_id)
    if e["progress"] >= e["count"]:
        who = e["npc"].replace("_", " ")
        return f"✉️ Поручение выполнено! Вернись к {who}, чтобы доложить."
    return f"✉️ Поручение ({name}): {e['progress']}/{e['count']}"


def can_turn_in(ch, npc_id: str) -> bool:
    """Готово ли активное поручение к сдаче ИМЕННО этому NPC."""
    e = ch.flags.get("errand")
    if not e or e.get("npc") != npc_id:
        return False
    if e["type"] == "kill":
        return e["progress"] >= e["count"]
    return ch.inventory.count(e["item"]) >= e["count"]


def turn_in(ch, npc_id: str) -> Optional[str]:
    """Сдать поручение выдавшему NPC: списать предметы, выдать награду, очистить."""
    if not can_turn_in(ch, npc_id):
        return None
    e = ch.flags["errand"]
    if e["type"] == "collect":
        for _ in range(e["count"]):
            if e["item"] in ch.inventory:
                ch.inventory.remove(e["item"])
    rew = e.get("reward", {})
    ch.xp += int(rew.get("xp", 0))
    ch.gold += int(rew.get("gold", 0))
    got = []
    for it in rew.get("items", []):
        ch.inventory.append(it)
        got.append((ITEMS.get(it) or {}).get("name", it))
    ch.flags.pop("errand", None)
    from . import money
    line = (f"✉️ Поручение сдано! +{rew.get('xp', 0)} опыта, "
            f"+{money.fmt(rew.get('gold', 0))}.")
    if got:
        line += "\n🎁 Награда: " + ", ".join(got)
    return line


def abandon(ch) -> str:
    """Бросить активное поручение (дневной счётчик НЕ откатывается)."""
    e = ch.flags.pop("errand", None)
    ch.flags.pop("errand_pending", None)
    if not e:
        return "У тебя нет активного поручения."
    return "✖️ Поручение брошено."


# ───────────────────────── РЕНДЕР ─────────────────────────
def _goal_line(e: dict) -> str:
    if e["type"] == "kill":
        name = (MOBS.get(e["mob"]) or {}).get("name", e["mob"])
        return f"🎯 Убить: {name} — {e['progress']}/{e['count']}"
    name = (ITEMS.get(e["item"]) or {}).get("name", e["item"])
    return f"🎯 Собрать: {name} — {e['count']} ед."


def brief(e: dict) -> str:
    """Короткая строка прогресса поручения для диалога NPC."""
    if e["type"] == "kill":
        name = (MOBS.get(e["mob"]) or {}).get("name", e["mob"])
        prog = f"{e['progress']}/{e['count']}"
        tail = " ✅ готово" if e["progress"] >= e["count"] else ""
        return f"✉️ Поручение: убить {name} — {prog}{tail}"
    name = (ITEMS.get(e["item"]) or {}).get("name", e["item"])
    return f"✉️ Поручение: собрать {name} — нужно {e['count']} ед."


def render(ch) -> str:
    """Блок активного поручения для экрана «Журнал». Пусто — если поручения нет."""
    e = ch.flags.get("errand")
    if not e:
        return ""
    from . import money
    who = e["npc"].replace("_", " ")
    L = ["✉️ *Поручение* — от " + who]
    if e["type"] == "kill":
        name = (MOBS.get(e["mob"]) or {}).get("name", e["mob"])
        done = e["progress"] >= e["count"]
        L.append(f"🎯 Убить: {name} — {e['progress']}/{e['count']}"
                 + ("  ✅ готово к сдаче" if done else ""))
    else:
        name = (ITEMS.get(e["item"]) or {}).get("name", e["item"])
        have = ch.inventory.count(e["item"])
        done = have >= e["count"]
        L.append(f"🎯 Собрать: {name} — {have}/{e['count']}"
                 + ("  ✅ готово к сдаче" if done else ""))
    rew = e.get("reward", {})
    rparts = [f"{rew.get('xp', 0)} опыта", f"💰{money.fmt(rew.get('gold', 0))}"]
    if rew.get("items"):
        rparts.append(", ".join((ITEMS.get(i) or {}).get("name", i) for i in rew["items"]))
    L.append("🎁 Награда: " + ", ".join(rparts))
    return "\n".join(L)
