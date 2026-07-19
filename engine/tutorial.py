# -*- coding: utf-8 -*-
"""
Онбординг-цепочка для новичков (уровень ≤5). Мягко ведёт игрока через базовые
механики: перемещение, атака, умение, зелье, сдача квеста. За каждый первый раз
даёт короткую подсказку следующего шага и микронаграду; в сумме награды ≈ полу-
уровень. После всех шагов — финальное сообщение и дешёвый аксессуар-кольцо.

Чистый модуль без Telegram (стиль daily.py). Состояние — в ch.flags["tut"]:
    {"done": [шаги], "finished": bool}

Легаси-игроки (уровень >5, туториал не начат) — никогда не стартуют.
"""
from .content import ITEMS
from . import money

# Порядок шагов онбординга. Событие приходит из bot/main при первом действии.
STEPS = ["move", "attack", "skill", "potion", "quest"]

# Финальный бонус-аксессуар: дешёвое кольцо без цены (не продаётся в лавках),
# универсальный лёгкий бонус — подходит любому классу.
FINISH_ITEM = "серебряное_кольцо"

# Награда за каждый пройденный шаг. Суммарно ≈ полуровень (xp_to_next на 1 ур. ≈ 52,
# на 2 ур. ≈ 110): 15+20+25+30 = 90 опыта + зелья + немного золота (в бронзе).
# quest-шаг награду опытом/золотом не дублирует — сам квест уже платит; за него идёт
# финальный бонус. Золото хранится в бронзе (money.fmt делит на 100 при показе).
_REWARDS = {
    "move":   {"xp": 15, "gold": 1000},                       # +10 монет
    "attack": {"xp": 20, "gold": 1500},                       # +15 монет
    "skill":  {"xp": 25, "potions": 1},                       # +1 малое зелье
    "potion": {"xp": 30, "potions": 1, "gold": 2000},         # +1 зелье, +20 монет
    "quest":  {},                                             # финал: только бонус-кольцо
}

# Подсказка СЛЕДУЮЩЕГО шага (показывается после прохождения текущего).
_NEXT_HINT = {
    "move":   "🎯 *Дальше:* найди монстра и нажми ⚔️, чтобы напасть.",
    "attack": "🎯 *Дальше:* открой ⚔️ умения и примени боевой навык в бою.",
    "skill":  "🎯 *Дальше:* выпей 🧪 зелье лечения из сумки, когда HP просядет.",
    "potion": "🎯 *Дальше:* возьми задание у NPC (💬) и сдай его — это последний шаг обучения.",
    "quest":  None,   # после квеста — финал
}

POTION_ITEM = "малое_зелье"


def _state(ch) -> dict:
    """Вернуть (создав при необходимости) состояние туториала."""
    t = ch.flags.get("tut")
    if not isinstance(t, dict):
        t = {"done": [], "finished": False}
        ch.flags["tut"] = t
    t.setdefault("done", [])
    t.setdefault("finished", False)
    return t


def _eligible(ch) -> bool:
    """Может ли игрок участвовать в туториале.

    - если туториал уже начат (есть done или finished) — да, ведём до конца
      (даже если игрок успел перешагнуть 5 ур. по ходу обучения);
    - иначе стартуем только новичкам ≤5 ур. Легаси-игроки (>5 ур. без начатого
      туториала) не втягиваются никогда.
    """
    t = ch.flags.get("tut")
    started = isinstance(t, dict) and (t.get("done") or t.get("finished"))
    if started:
        return True
    return ch.level <= 5


def _grant(ch, reward: dict) -> list:
    """Выдать микронаграду за шаг. Возвращает строки для показа игроку."""
    lines = []
    parts = []
    xp = reward.get("xp", 0)
    if xp:
        ch.xp += xp
        parts.append(f"✨{xp} опыта")
    gold = reward.get("gold", 0)
    if gold:
        ch.gold += gold
        parts.append(f"💰{money.fmt(gold)}")
    pots = reward.get("potions", 0)
    for _ in range(pots):
        ch.inventory.append(POTION_ITEM)
    if pots:
        pname = ITEMS.get(POTION_ITEM, {}).get("name", POTION_ITEM)
        parts.append(f"{pname} ×{pots}")
    if parts:
        lines.append("🎁 Награда обучения: " + ", ".join(parts) + ".")
    return lines


def _finish(ch, t: dict) -> list:
    """Завершить туториал: выдать бонус-кольцо и финальное сообщение."""
    t["finished"] = True
    lines = ["🎓 *Обучение завершено!* Ты освоил основы — дальше мир твой."]
    item = FINISH_ITEM if FINISH_ITEM in ITEMS else None
    if item:
        ch.inventory.append(item)
        iname = ITEMS[item].get("name", item)
        lines.append(f"🏅 Награда за выпуск: *{iname}* — надень его в 🎒 сумке.")
    return lines


def on_event(ch, event: str) -> list:
    """Обработать событие обучения. При ПЕРВОМ срабатывании шага вернуть строки:
    награда + подсказка следующего шага (или финал). Иначе — пустой список.

    Идемпотентно: повторные события того же шага наград не дают (dedup по done).
    """
    if event not in STEPS:
        return []
    if not _eligible(ch):
        return []
    t = _state(ch)
    if t.get("finished"):
        return []
    if event in t["done"]:
        return []
    t["done"].append(event)

    lines = _grant(ch, _REWARDS.get(event, {}))
    hint = _NEXT_HINT.get(event)
    if hint:
        lines.append(hint)
    # финал — когда пройдены все шаги
    if all(s in t["done"] for s in STEPS):
        lines += _finish(ch, t)
    return lines


def render(ch) -> str:
    """Строка прогресса обучения для экрана помощи."""
    t = ch.flags.get("tut")
    labels = {
        "move": "🚶 Перемещение",
        "attack": "⚔️ Атака",
        "skill": "✨ Умение",
        "potion": "🧪 Зелье",
        "quest": "📜 Квест",
    }
    if not isinstance(t, dict) or not (t.get("done") or t.get("finished")):
        if ch.level > 5:
            return ""   # легаси-игрок: обучение не показываем
        return ("🎓 *Обучение новичка*\nСделай первые шаги — за каждый идёт награда:\n" +
                "\n".join(f"⬜️ {labels[s]}" for s in STEPS))
    done = set(t.get("done", []))
    L = ["🎓 *Обучение новичка*"]
    for s in STEPS:
        mark = "✅" if s in done else "⬜️"
        L.append(f"{mark} {labels[s]}")
    if t.get("finished"):
        L.append("\n🏅 Курс пройден — так держать!")
    else:
        left = [labels[s] for s in STEPS if s not in done]
        if left:
            L.append("\n_Осталось: " + ", ".join(left) + "._")
    return "\n".join(L)
