# -*- coding: utf-8 -*-
"""
Структурный логгер поверх стандартного logging.

Мотив: по критическим путям бота исключения глушились голым `except: pass`
или уходили в `print(...)` без контекста — при закрытой бете это слепые зоны.
Здесь — тонкая обёртка: get(name) отдаёт обычный логгер, а log_err пишет
событие в формате «event key=value …» и, если передан err, прикладывает стек.

Живёт в engine/ и НИЧЕГО не тянет из bot/ai — тестируется без aiogram.
Секреты (токены/ключи) сюда не передаём: логируем только безопасный контекст.
"""
import json
import logging
import os
import sys
import time
from collections import deque

_CONFIGURED = False


def _json_enabled() -> bool:
    """LOG_JSON=1 → структурные логи одной JSON-строкой (для сбора в проде)."""
    return (os.environ.get("LOG_JSON") or "").strip() in ("1", "true", "True", "yes", "on")


class _JsonFormatter(logging.Formatter):
    """Каждая запись — одна строка JSON: {ts, level, logger, event, <ctx…>, exc?}.

    event берётся из structured-поля record.event (его кладёт log_err); для
    обычных logger.info(...) без структурного контекста event = отформатированное
    сообщение. Ключи ctx (uid, category, cid…) поднимаются в верхний уровень —
    так их удобно фильтровать в агрегаторе логов. Секреты сюда не попадают: в ctx
    их не передают (см. политику модуля)."""

    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": round(record.created, 3),
            "level": record.levelname,
            "logger": record.name,
        }
        event = getattr(record, "event", None)
        out["event"] = event if event is not None else record.getMessage()
        ctx = getattr(record, "ctx", None)
        if isinstance(ctx, dict):
            for k, v in ctx.items():
                if k not in out:
                    out[k] = v
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False, default=str)

# Кольцевой буфер последних error-событий (Этап 7.2): для админ-экрана «💚 Health».
# log_err складывает сюда каждое ЗАРЕГИСТРИРОВАННОЕ событие с исключением (err не
# None) — админ видит последние сбои прямо из бота, без доступа к файлам логов.
_ERR_RING_MAX = 20
_ERR_RING = deque(maxlen=_ERR_RING_MAX)


def recent_errors(limit: int = _ERR_RING_MAX):
    """Последние error-события -> [{'ts','event','ctx','err'}, ...], свежие первыми."""
    items = list(_ERR_RING)[-int(limit):]
    return list(reversed(items))


def clear_errors():
    """Очистить буфер ошибок (для тестов/ручного сброса)."""
    _ERR_RING.clear()


def _ensure_configured() -> None:
    """Ленивая базовая конфигурация корневого логгера (один раз за процесс).
    Если приложение уже настроило logging само — не перетираем хендлеры."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    root = logging.getLogger()
    if _json_enabled():
        # Структурный режим: ставим один stdout-хендлер с JSON-форматтером.
        # Если приложение уже навесило хендлеры — переводим ИХ форматтер в JSON
        # (не плодим дублирующий вывод).
        fmt = _JsonFormatter()
        if not root.handlers:
            h = logging.StreamHandler(stream=sys.stdout)
            h.setFormatter(fmt)
            root.addHandler(h)
        else:
            for h in root.handlers:
                h.setFormatter(fmt)
        root.setLevel(logging.INFO)
    elif not root.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
    _CONFIGURED = True


def get(name: str) -> logging.Logger:
    """Вернуть именованный логгер (напр. get('bot.main'))."""
    _ensure_configured()
    return logging.getLogger(name)


def _fmt_ctx(ctx: dict) -> str:
    """Сериализовать контекст в «key=value key=value» (значения без переносов)."""
    parts = []
    for k, v in ctx.items():
        sv = str(v).replace("\n", " ").replace("\r", " ")
        parts.append(f"{k}={sv}")
    return " ".join(parts)


def log_err(logger: logging.Logger, event: str, err: BaseException = None, **ctx) -> None:
    """Записать структурное событие ошибки/предупреждения.

    event — короткий машиночитаемый ключ события (напр. 'char_flush_failed');
    err   — исключение (если есть): будет приложен полный стек (exc_info);
    ctx   — безопасный контекст (uid, category, attempt …). Секреты не передавать!

    Уровень: ERROR при наличии err, иначе WARNING (событие без исключения —
    это, как правило, деградация/предупреждение, а не пойманный сбой)."""
    msg = event
    if ctx:
        msg = f"{event} {_fmt_ctx(ctx)}"
    # structured-поля для JSON-режима (в человекочитаемом формате не видны —
    # там показывается уже собранный msg). event/ctx — не reserved-имена LogRecord.
    extra = {"event": event, "ctx": ctx or {}}
    if err is not None:
        logger.error(msg, exc_info=err, extra=extra)
        # положить в кольцевой буфер для админ-Health (только события со сбоем)
        _ERR_RING.append({
            "ts": time.time(),
            "event": event,
            "ctx": _fmt_ctx(ctx) if ctx else "",
            "err": f"{type(err).__name__}: {err}"[:200],
        })
    else:
        logger.warning(msg, extra=extra)
