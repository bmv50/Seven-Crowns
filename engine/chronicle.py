# -*- coding: utf-8 -*-
"""Хроника мира: общий лог значимых событий, который видят игроки (экран
«📜 Хроника мира») и «знают» ИИ-NPC (сплетничают о недавних событиях в
диалогах, см. ai/npc_ai.py). Кольцевой буфер в памяти процесса — состояние
персистится так же, как territory.py (kv_state['chronicle'] в snapshot_worker).

Типы записей: boss (мировой/рейд-босс пал), territory (зона перешла фракции),
season (новый сезон начался), event (мировое событие началось/кончилось),
legend (игрок взял лигу Легенда), collection (собрана коллекция с титулом).
"""
import time
from collections import deque

# Кольцевой буфер записей {ts, type, text}. Свежие добавляются справа —
# recent()/render() отдают их в порядке «свежие первыми» (реверс при чтении).
_LOG_MAXLEN = 60
_LOG = deque(maxlen=_LOG_MAXLEN)

# Дедуп-реестр ключей для record_once: (etype, key) -> True. Ограничен по
# размеру тем же способом (deque с maxlen), чтобы не течь памятью бесконечно;
# хранит порядок вставки для вытеснения самых старых ключей.
_SEEN_MAXLEN = 200
_SEEN: dict = {}
_SEEN_ORDER = deque(maxlen=_SEEN_MAXLEN)

# «Летопись» — эпический текст итогов минувшего сезона (пишет ai/god.py при
# ролловере: LLM или шаблон). Одна строка на процесс; персистится вместе с
# логом в kv_state['chronicle'] (export/import_state). Показывается первой
# секцией в render(). None → секции нет.
_EPIC: str = None

# Порог обрезки одной записи для NPC-промпта (беречь токены, см. recent()).
_RECENT_TRUNC = 90

# Режим хранения (как в territory/auction). В db-режиме save-путей нет —
# persist делает bot/main.py напрямую через export_state()/import_state(),
# но is_dirty()/mark_clean() нужны для того же протокола flush по dirty-флагу.
_db_mode = False
_dirty = False


def set_db_mode(on: bool):
    """Включить БД-режим (см. bot/main.py: chronicle.set_db_mode(True) при БД)."""
    global _db_mode
    _db_mode = bool(on)


def is_dirty() -> bool:
    return _dirty


def mark_clean():
    global _dirty
    _dirty = False


def _mark_dirty():
    global _dirty
    _dirty = True


def record(etype: str, text: str, ts: float = None):
    """Добавить запись в хронику. ts — для тестов (детерминизм); по умолчанию now()."""
    _LOG.append({"ts": ts if ts is not None else time.time(), "type": etype, "text": text})
    _mark_dirty()


def record_once(etype: str, key: str, text: str, ts: float = None):
    """Как record(), но не дублирует одно и то же событие: если (etype, key)
    уже встречались — молча пропускаем. Полезно для событий с уникальным
    идентификатором (сезон N, лига «Легенда» игрока X и т.п.)."""
    seen_key = (etype, key)
    if seen_key in _SEEN:
        return
    if len(_SEEN_ORDER) >= _SEEN_MAXLEN:
        oldest = _SEEN_ORDER.popleft()
        _SEEN.pop(oldest, None)
    _SEEN[seen_key] = True
    _SEEN_ORDER.append(seen_key)
    record(etype, text, ts)


def get_epic() -> str:
    """Текущая «Летопись» (эпические итоги минувшего сезона) или None."""
    return _EPIC


def set_epic(text: str):
    """Записать «Летопись» (перезаписывает предыдущую). Пустой текст → сброс."""
    global _EPIC
    _EPIC = (text or "").strip() or None
    _mark_dirty()


def _relative_time(ts: float, now: float = None) -> str:
    """Человекочитаемое относительное время («2ч назад», «только что»)."""
    now = now if now is not None else time.time()
    delta = max(0, int(now - ts))
    if delta < 60:
        return "только что"
    if delta < 3600:
        return f"{delta // 60}м назад"
    if delta < 86400:
        return f"{delta // 3600}ч назад"
    days = delta // 86400
    return f"{days}д назад"


def _truncate(text: str, limit: int = _RECENT_TRUNC) -> str:
    if len(text) <= limit:
        return text
    return text[:max(0, limit - 1)].rstrip() + "…"


def recent(n: int = 6) -> list:
    """Последние n текстов (свежие первыми), обрезанные до _RECENT_TRUNC
    символов — для промпта NPC (беречь токены)."""
    out = []
    for rec in reversed(_LOG):
        out.append(_truncate(rec["text"]))
        if len(out) >= n:
            break
    return out


def render(n: int = 12) -> str:
    """«📜 Хроника мира» — до n записей с относительным временем, свежие первыми.
    Если есть «Летопись» (итоги минувшего сезона) — она идёт первой секцией."""
    lines = []
    if _EPIC:
        lines.append("🏛 *Летопись*")
        lines.append("")
        lines.append(_EPIC)
        lines.append("")
    if not _LOG:
        if lines:      # летопись есть, но текущих записей ещё нет
            lines.append("📜 *Хроника мира*")
            lines.append("")
            lines.append("_Пока тихо — значимых событий не случалось._")
            return "\n".join(lines)
        return "📜 *Хроника мира*\n\n_Пока тихо — значимых событий не случалось._"
    now = time.time()
    lines.append("📜 *Хроника мира*")
    lines.append("")
    for rec in list(reversed(_LOG))[:n]:
        lines.append(f"• {rec['text']} _({_relative_time(rec['ts'], now)})_")
    return "\n".join(lines)


def export_state() -> dict:
    """JSON-safe снимок хроники (для kv_state['chronicle'])."""
    return {
        "log": list(_LOG),
        "seen": [list(k) for k in _SEEN_ORDER],
        "epic": _EPIC,
    }


def import_state(data: dict):
    """Загрузить хронику из снимка (kv_state) вместо файла."""
    global _LOG, _SEEN, _SEEN_ORDER, _EPIC, _dirty
    _LOG = deque(maxlen=_LOG_MAXLEN)
    for rec in (data or {}).get("log", []) or []:
        # JSON превращает кортежи в списки, но сама запись — словарь, ок как есть
        _LOG.append({"ts": rec.get("ts", 0.0), "type": rec.get("type", ""), "text": rec.get("text", "")})
    _SEEN = {}
    _SEEN_ORDER = deque(maxlen=_SEEN_MAXLEN)
    for pair in (data or {}).get("seen", []) or []:
        if len(pair) == 2:
            key = (pair[0], pair[1])
            _SEEN[key] = True
            _SEEN_ORDER.append(key)
    _epic = (data or {}).get("epic")
    _EPIC = (_epic or "").strip() or None if _epic else None
    _dirty = False


def reset():
    """Полностью очистить хронику (используется тестами)."""
    global _LOG, _SEEN, _SEEN_ORDER, _EPIC, _dirty
    _LOG = deque(maxlen=_LOG_MAXLEN)
    _SEEN = {}
    _SEEN_ORDER = deque(maxlen=_SEEN_MAXLEN)
    _EPIC = None
    _dirty = False
