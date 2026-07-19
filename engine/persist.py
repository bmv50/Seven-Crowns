# -*- coding: utf-8 -*-
"""
Помощники персистентности рантайма, не зависящие от aiogram/Telegram.

CharDirtySet — дебаунс сохранения персонажей. Раньше bot.save(ch) писал в БД
на КАЖДОЕ действие игрока (лишняя нагрузка). Теперь обычный save лишь помечает
uid «грязным», а фоновый флашер раз в несколько секунд пишет всех грязных
батчем. Транзакционно важные моменты (создание персонажа, реморт, покупка/
продажа/аукцион/крафт, награды рефералки и claim'ы) сохраняются немедленно
(force=True) — там нельзя терять данные при внезапном рестарте.

Класс намеренно живёт в engine/ и НЕ импортирует ничего из bot/ — чтобы его
можно было протестировать без aiogram (см. test_persistence.py).
"""
import asyncio
import time as _time
from typing import Awaitable, Callable, Optional, Set

from . import log as _log


class CharDirtySet:
    """Набор uid персонажей, ожидающих отложенной записи в БД.

    Использование в bot.main:
        _dirty = CharDirtySet()
        # обычный save: пометить грязным (запись отложена флашеру)
        _dirty.mark(ch.uid)
        # флашер раз в N секунд:
        for uid in _dirty.drain():
            ch = chars.get(uid)
            if ch: await db.save(ch)
    """

    def __init__(self):
        self._dirty: Set[int] = set()

    def mark(self, uid: int) -> None:
        """Пометить персонажа как требующего сохранения."""
        self._dirty.add(uid)

    def discard(self, uid: int) -> None:
        """Убрать uid из очереди (напр. после немедленной force-записи)."""
        self._dirty.discard(uid)

    def pending(self) -> Set[int]:
        """Копия текущего набора грязных uid (без опустошения)."""
        return set(self._dirty)

    def has(self, uid: int) -> bool:
        return uid in self._dirty

    def __len__(self) -> int:
        return len(self._dirty)

    def __contains__(self, uid) -> bool:
        return uid in self._dirty

    def drain(self) -> Set[int]:
        """Вернуть все грязные uid и очистить набор (атомарно на уровне GIL).
        Флашер вызывает это, затем пишет каждого персонажа в БД."""
        out = self._dirty
        self._dirty = set()
        return out


# ───────────────────────── флашер: запись + возврат провалов ─────────────────────────
# Раньше flush_dirty_chars при исключении db.save() лишь печатал ошибку и ТЕРЯЛ uid
# (drain уже опустошил набор) — прогресс игрока пропадал молча. Теперь логика записи
# вынесена сюда, в чистую часть: провалившиеся uid ВОЗВРАЩАЮТСЯ в набор (не теряем),
# ошибки логируются структурно, а на shutdown есть ретрай. Тестируется с моком save.

async def flush_dirty(
    dirty: "CharDirtySet",
    get_char: Callable[[int], object],
    save: Callable[[object], Awaitable[None]],
    logger=None,
    event: str = "char_flush_failed",
) -> tuple:
    """Записать всех накопившихся грязных персонажей батчем.

    get_char(uid) -> персонаж | None (пропущенные None не считаются провалом);
    save(ch)      -> корутина записи в БД (может бросить исключение).

    Провалившиеся uid возвращаются в набор (dirty.mark) — их добьёт следующий
    проход/ретрай, данные не теряются. Возвращает (ok, failed)."""
    ok = 0
    failed = 0
    for uid in dirty.drain():
        ch = get_char(uid)
        if ch is None:
            continue
        try:
            await save(ch)
            ok += 1
        except Exception as e:              # noqa: BLE001 — намеренно широкий: не роняем флашер
            dirty.mark(uid)                 # вернуть uid — не теряем прогресс игрока
            failed += 1
            if logger is not None:
                _log.log_err(logger, event, e, uid=uid)
    return ok, failed


async def flush_until_clean(
    dirty: "CharDirtySet",
    get_char: Callable[[int], object],
    save: Callable[[object], Awaitable[None]],
    attempts: int = 3,
    pause: float = 1.0,
    sleeper: Optional[Callable[[float], Awaitable[None]]] = None,
    logger=None,
) -> tuple:
    """Graceful shutdown: добить провалившихся до `attempts` попыток с паузой
    `pause` между ними. Возвращает (ok_total, оставшихся_грязных).

    sleeper — инъекция asyncio.sleep (в тестах передаём мгновенную заглушку)."""
    if sleeper is None:
        sleeper = asyncio.sleep
    ok_total = 0
    for i in range(max(1, attempts)):
        ok, failed = await flush_dirty(
            dirty, get_char, save, logger=logger, event="char_flush_shutdown_failed")
        ok_total += ok
        if failed == 0 or len(dirty) == 0:
            break
        if i < attempts - 1:
            await sleeper(pause)
    return ok_total, len(dirty)


class FlushHealth:
    """Здоровье фонового флашера: считает ПОДРЯД провальные проходы и решает,
    пора ли громко предупредить. Порог — 5 провалов подряд; предупреждение не
    чаще раза в warn_interval секунд (защита от спама в лог)."""

    def __init__(self, threshold: int = 5, warn_interval: float = 60.0, clock=None):
        self.threshold = threshold
        self.warn_interval = warn_interval
        self.consecutive = 0
        self._last_warn = 0.0
        self._clock = clock or _time.monotonic

    def record(self, failed: int) -> bool:
        """Учесть результат прохода (сколько персонажей не записалось).
        Вернуть True, если пора выдать громкое предупреждение."""
        if failed <= 0:
            self.consecutive = 0
            return False
        self.consecutive += 1
        if self.consecutive < self.threshold:
            return False
        now = self._clock()
        if now - self._last_warn >= self.warn_interval:
            self._last_warn = now
            return True
        return False


# ───────────────────────── reset-flow: чистая проверка окна ─────────────────────────
RESET_WINDOW_SEC = 60.0


def reset_pending_valid(ts, now: float, window: float = RESET_WINDOW_SEC) -> bool:
    """True, если запрос /reset (сделанный в момент ts) ещё в окне подтверждения
    и не просрочен. ts=None (запроса не было) → False. Вынесено в чистую функцию
    для тестируемости без aiogram."""
    if ts is None:
        return False
    return 0 <= (now - ts) <= window
