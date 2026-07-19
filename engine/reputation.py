# -*- coding: utf-8 -*-
"""
Репутация с фракциями. Хранится в ch.flags["rep"] = {faction_id: очки}.
Растёт за выполнение заданий фракции. Высокая репутация даёт скидку у её торговцев.
"""
from .content import FACTIONS
from . import weekly

# (порог, название, эмодзи) по возрастанию
_TIERS = [
    (-10**9, "Враждебность", "💢"),
    (0, "Нейтралитет", "➖"),
    (500, "Дружелюбие", "🙂"),
    (1500, "Уважение", "🤝"),
    (4000, "Почтение", "🌟"),
]


def points(ch, fac: str) -> int:
    return int((ch.flags.get("rep") or {}).get(fac, 0))


def tier(p: int):
    name, emoji = "Нейтралитет", "➖"
    for thr, n, e in _TIERS:
        if p >= thr:
            name, emoji = n, e
    return name, emoji


def gain(ch, fac: str, amount: int):
    """Начислить очки репутации фракции. -> строка недельного прогресса (или
    None), если положительный прирост продвинул задачу faction_rep (Этап 6.1).
    Возврат ранее не использовался вызывающими — добавлен без риска регрессии."""
    if not fac:
        return None
    rep = ch.flags.setdefault("rep", {})
    rep[fac] = int(rep.get(fac, 0)) + amount
    if amount > 0:
        return weekly.on_faction_rep(ch, amount)
    return None


def discount(ch, fac: str) -> float:
    """Скидка у торговцев фракции по репутации."""
    p = points(ch, fac)
    if p >= 4000:
        return 0.15
    if p >= 1500:
        return 0.10
    if p >= 500:
        return 0.05
    return 0.0


def render(ch) -> str:
    rep = ch.flags.get("rep") or {}
    L = ["🤝 *Репутация с фракциями*", ""]
    if not rep:
        L.append("_Вы пока нейтральны со всеми. Выполняйте задания фракций, "
                 "чтобы заслужить их расположение и скидки у торговцев._")
        return "\n".join(L)
    for fac, p in sorted(rep.items(), key=lambda x: -x[1]):
        fname = FACTIONS.get(fac, {}).get("name", fac)
        n, e = tier(p)
        disc = discount(ch, fac)
        dtag = f"  (скидка {int(disc*100)}%)" if disc else ""
        L.append(f"{e} *{fname}*: {n} — {p}{dtag}")
    return "\n".join(L)
