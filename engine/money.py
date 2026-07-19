# -*- coding: utf-8 -*-
"""
Деньги: всё хранится в БРОНЗЕ (наименьшая монета).
Курс: 1 золотая = 100 серебряных = 10000 бронзовых.
ch.gold — это суммарное количество бронзы.
"""
GOLD = 10000     # бронзы в золотой
SILVER = 100     # бронзы в серебряной


def split(amount: int):
    """бронза -> (золото, серебро, бронза)."""
    amount = max(0, int(amount))
    g, amount = divmod(amount, GOLD)
    s, b = divmod(amount, SILVER)
    return g, s, b


COIN = 100   # внутренних единиц в одной отображаемой «монете»


def fmt(amount: int) -> str:
    """Единая валюта «монеты»: внутр. ед. / 100, округление, без нулей для >0."""
    a = max(0, int(amount))
    if a == 0:
        return "0"
    coins = max(1, round(a / COIN))
    return f"{coins:,}".replace(",", " ")


def fmt_coins(amount: int) -> str:
    """С монетным значком: '🪙 4 319 800'."""
    return f"🪙 {fmt(amount)}"


def gold(n: int) -> int:
    return n * GOLD


def silver(n: int) -> int:
    return n * SILVER
