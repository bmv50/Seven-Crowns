# -*- coding: utf-8 -*-
"""
Покупные титулы-косметика (золотосток эндгейма).

Богатые игроки тратят накопленное золото на престижные звания без боевого
преимущества — чистый сток валюты и статус. Титул добавляется в
ch.flags["extra_titles"] (список), который уже считается валидным для
achievements.set_title. Покупка списывает золото; повторная покупка того же
титула невозможна. Логика без Telegram — тестируется в engine/.
"""
from . import money

# id -> {name (сам титул), price (внутр. бронза), desc}
# Цены (спринт 6, после ребаланса цен экипировки): 10M / 18M / 30M внутр. =
# 100 000 / 180 000 / 300 000 отображаемых монет. Якорь — снаряжение 45–60 ур.:
# «средний предмет» (~67k монет) → «топ-предмет уровня» (~184k) → «люкс, дороже
# любого шмота» (чистый престиж). Раньше титулы стоили 500/2000/5000 монет — при
# новых ценах вещей это была бы мелочь на карман; подняты, чтобы остаться
# осмысленным золотостоком эндгейма для богатых игроков.
# Названия — в духе мира «Эхо Глубин» (меценатство, покровительство столицам).
TITLES = {
    "patron_aeldmar": {
        "name": "Меценат Аэльдмара",
        "price": 10_000_000,
        "desc": "Щедрый благодетель, чьё золото держит на плаву торговые ряды столицы.",
    },
    "gilded_lord": {
        "name": "Златоносный Владыка",
        "price": 18_000_000,
        "desc": "Имя, при котором звенят сундуки; купцы кланяются раньше, чем видят лицо.",
    },
    "abyss_benefactor": {
        "name": "Благодетель Бездны",
        "price": 30_000_000,
        "desc": "Тот, кто откупает саму Глубь: легенда гильдий и ужас нищих сборщиков податей.",
    },
}


def owned(ch, tid: str) -> bool:
    """Куплен ли титул tid этим игроком."""
    t = TITLES.get(tid)
    if not t:
        return False
    return t["name"] in (ch.flags.get("extra_titles") or [])


def can_buy(ch, tid: str):
    """(ok, причина-если-нет). Проверка без списания."""
    t = TITLES.get(tid)
    if not t:
        return False, "Такого титула нет в лавке."
    if owned(ch, tid):
        return False, "Этот титул у вас уже есть."
    if ch.gold < t["price"]:
        need = t["price"] - ch.gold
        return False, f"Не хватает {money.fmt(need)} монет."
    return True, ""


def buy(ch, tid: str):
    """Купить титул: списать золото, добавить в extra_titles. -> (ok, сообщение).
    Идемпотентно к повтору (второй раз вернёт отказ, деньги не спишет)."""
    ok, why = can_buy(ch, tid)
    if not ok:
        return False, why
    t = TITLES[tid]
    ch.gold -= t["price"]
    ch.flags.setdefault("extra_titles", [])
    if t["name"] not in ch.flags["extra_titles"]:
        ch.flags["extra_titles"].append(t["name"])
    return True, (f"🎖 Вы приобрели титул «{t['name']}» за 💰{money.fmt(t['price'])}. "
                  f"Наденьте его в разделе титулов. (Осталось {money.fmt(ch.gold)})")


def for_shop(ch):
    """Список товаров лавки титулов: [(tid, name, price, owned_bool)]."""
    out = []
    for tid, t in TITLES.items():
        out.append((tid, t["name"], t["price"], owned(ch, tid)))
    return out
