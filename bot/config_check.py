# -*- coding: utf-8 -*-
"""
Этап 9: валидация окружения на старте — чистая, тестируемая, БЕЗ aiogram.

Мотив: «production fail fast» регламента — процесс должен падать сразу с
понятным сообщением, а не спустя минуты словить `TelegramUnauthorizedError`
на первом запросе или уронить экономику из-за отсутствия БД в PROD.

check_config(env) принимает словарь окружения (по умолчанию os.environ) и
возвращает результат: список фатальных ошибок (errors) и предупреждений
(warnings). bot/main.py на старте зовёт validate_or_die() — при errors печатает
их и делает sys.exit(1). Функция ничего не импортирует из bot/ai и не трогает
сеть/БД — её гоняет test_deploy.py без телеги и без Postgres.

ГРАНИЦЫ (осознанно): опечатки в НЕизвестных именах переменных (напр. BOT_TOEKN)
здесь НЕ ловятся — у нас нет закрытого списка всех возможных опций (флаги мира,
AI_*, PROXY_URL, GOD_INTERVAL…), а чёрный список ложно ругался бы на легальные
кастомные переменные. Ловим только то, без чего игра ГАРАНТИРОВАННО не поедет.
"""
from __future__ import annotations

import os
from typing import Dict, List, NamedTuple, Optional


# Заглушки-плейсхолдеры из .env.example — если BOT_TOKEN всё ещё равен одной из
# них (или начинается с узнаваемого префикса), значит .env не заполнен.
_TOKEN_PLACEHOLDERS = (
    "PASTE_YOUR_TOKEN_HERE",
    "ВСТАВЬ_СВОЙ_ТОКЕН",
)
_PLACEHOLDER_PREFIXES = ("ВСТАВЬ_", "PASTE_", "YOUR_", "ЗАПОЛНИ")


def _is_truthy(val: Optional[str]) -> bool:
    return (val or "").strip() in ("1", "true", "True", "yes", "on")


def _is_placeholder_token(tok: str) -> bool:
    """Токен пуст, равен известной заглушке или несёт узнаваемый префикс-заглушку."""
    t = (tok or "").strip()
    if not t:
        return True
    if t in _TOKEN_PLACEHOLDERS:
        return True
    up = t.upper()
    return any(up.startswith(p) for p in _PLACEHOLDER_PREFIXES)


def parse_admin_ids(raw: str):
    """Разобрать ADMIN_IDS: список uid через запятую/точку-с-запятой/пробел.
    Возвращает (валидные_id:set[int], мусорные_токены:list[str]).

    Дубль логики bot.main._parse_admin_ids, но с раздельным учётом «мусора» —
    чтобы валидатор мог предупредить о непарсибельных токенах (человек вписал
    @username или мусор), не роняя старт (ADMIN_IDS — не фатально)."""
    good = set()
    bad: List[str] = []
    for tok in (raw or "").replace(";", ",").replace(" ", ",").split(","):
        tok = tok.strip()
        if not tok:
            continue
        if tok.lstrip("-").isdigit():
            good.add(int(tok))
        else:
            bad.append(tok)
    return good, bad


class ConfigResult(NamedTuple):
    errors: List[str]
    warnings: List[str]

    @property
    def ok(self) -> bool:
        return not self.errors


def check_config(env: Optional[Dict[str, str]] = None) -> ConfigResult:
    """Проверить окружение. НЕ бросает исключений и не трогает I/O — только
    анализирует переданный словарь (по умолчанию — реальный os.environ)."""
    if env is None:
        env = dict(os.environ)
    errors: List[str] = []
    warnings: List[str] = []

    prod = _is_truthy(env.get("PROD"))

    # 1) BOT_TOKEN обязателен и не должен быть заглушкой
    if _is_placeholder_token(env.get("BOT_TOKEN", "")):
        errors.append(
            "BOT_TOKEN не задан или оставлен заглушкой. Получите токен у @BotFather "
            "и пропишите BOT_TOKEN в .env (или в окружении).")

    # 2) В PROD=1 обязателен DATABASE_URL (публичная игра без БД запрещена)
    db_url = (env.get("DATABASE_URL") or "").strip()
    if prod:
        if not db_url:
            errors.append(
                "PROD=1, но DATABASE_URL не задан. Публичная бета без PostgreSQL "
                "запрещена (экономика/аукцион транзакционны). Укажите DATABASE_URL.")
        elif _is_placeholder_token(db_url) or "ВСТАВЬ" in db_url.upper():
            errors.append(
                "PROD=1, но DATABASE_URL выглядит как незаполненная заглушка. "
                "Пропишите реальную строку подключения PostgreSQL.")
    else:
        if not db_url:
            warnings.append(
                "DATABASE_URL не задан (PROD=0): игра запустится БЕЗ сохранения "
                "прогресса (dev-режим в памяти).")

    # 3) ADMIN_IDS — не фатально, но предупреждаем о мусорных токенах
    good, bad = parse_admin_ids(env.get("ADMIN_IDS", ""))
    if bad:
        warnings.append(
            "ADMIN_IDS: не распознаны как uid и проигнорированы: "
            + ", ".join(bad[:10])
            + " (нужны числовые Telegram id через запятую).")
    if not good:
        warnings.append(
            "ADMIN_IDS пуст — админ-панель (/admin: баны, компенсации, Health, "
            "пауза торговли) будет недоступна.")

    return ConfigResult(errors=errors, warnings=warnings)


def validate_or_die(env: Optional[Dict[str, str]] = None, *, printer=print,
                    exiter=None) -> ConfigResult:
    """Прогнать check_config и, при фатальных ошибках, напечатать их и выйти
    (sys.exit(1)). Предупреждения печатаются, но старт не блокируют.

    printer/exiter вынесены параметрами ради тестируемости (тест подменяет их и
    проверяет, что при заглушке BOT_TOKEN зовётся exit с ненулевым кодом)."""
    import sys
    if exiter is None:
        exiter = sys.exit
    res = check_config(env)
    for w in res.warnings:
        printer(f"⚠️  {w}")
    if res.errors:
        printer("❌ Ошибка конфигурации — старт невозможен:")
        for e in res.errors:
            printer(f"   • {e}")
        printer("Проверьте .env (см. .env.example) и переменные окружения.")
        exiter(1)
    return res
