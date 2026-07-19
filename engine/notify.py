# -*- coding: utf-8 -*-
"""
Push-реактивация: чистое ядро уведомлений (без сетевых зависимостей).

Слой engine/ не знает про доставку сообщений мессенджером — он лишь решает,
ЧТО и КОГДА можно отправить, применяя анти-спам-политику. Саму отправку
выполняет bot-слой, дёргая emit()/due() и рассылая по списку uid из БД.

Модель:
  • Каталог категорий (CATEGORIES) + человекочитаемые названия (LABELS).
  • Настройки игрока в ch.flags["notify"] = {категория: bool}, дефолт — всё вкл.
  • Квота: ≤ N push/сутки на игрока, N — персональный пресет из
    ch.flags["notify"]["limit"] ∈ LIMIT_PRESETS (дефолт DEFAULT_LIMIT=2),
    счётчик+дата — в ch.flags["notify_quota"], КРОМЕ auction_* (сделки не
    режем — игрок ждёт их лично).
  • Тихие часы 23:00–09:00 серверного времени: world_boss протухает (дроп),
    daily_reset/dungeon_ready/rested_full откладываются до 09:00. Игрок может
    отключить их тумблером ch.flags["notify"]["quiet_off"].
  • Очередь в памяти: emit() кладёт, due(now) отдаёт готовые к отправке.

Всё за флагом ENABLED — без него bot-слой очередь не трогает и поведение
игры не меняется.
"""
import time
from datetime import datetime, timezone

ENABLED = False

# ── часовой пояс игрока (Этап 7.2) ──
# Тихие часы считаются в ЛОКАЛЬНОМ времени игрока, а не сервера: push не должен
# приходить ночью по его времени. Смещение UTC хранится в ch.flags["notify"]
# ["tz_offset"] (кнопки в экране «🔔 Уведомления»). Диапазон −2..+12; серверный
# дефолт — +3 (МСК), берётся когда tz игрока не задан (или отсутствует ch).
TZ_MIN = -2
TZ_MAX = 12
DEFAULT_TZ = 3

# ── каталог категорий ──
CATEGORIES = [
    "daily_reset", "world_boss", "auction_sold", "auction_outbid",
    "dungeon_ready", "rested_full", "season_end_soon", "season_rollover",
    "guild_event", "world_event",
]
LABELS = {
    "daily_reset":     "📅 Новое задание дня",
    "world_boss":      "🐉 Мировой босс",
    "auction_sold":    "💰 Лот продан",
    "auction_outbid":  "📈 Ставку перебили",
    "dungeon_ready":   "🏰 Данж готов",
    "rested_full":     "💤 Отдых накоплен",
    "season_end_soon": "⏳ Сезон завершается",
    "season_rollover": "🏅 Итоги сезона",
    "guild_event":     "⚔️ События гильдии",
    "world_event":     "🌐 Мировое событие",
}

# сделки игрока идут вне суточного лимита (он их ждёт персонально)
_OFF_QUOTA = {"auction_sold", "auction_outbid"}
# в тихие часы: дропаем (протухает) vs откладываем до утра
_QUIET_DROP = {"world_boss"}
_QUIET_DEFER = {"daily_reset", "dungeon_ready", "rested_full"}

MAX_PER_DAY = 2        # legacy-алиас DEFAULT_LIMIT (см. ниже); ссылок в коде больше нет
QUIET_START = 23      # 23:00 включительно
QUIET_END = 9         # до 09:00

# очередь в памяти: список dict {uid, category, text, fire_at}
_QUEUE = []


# ───────── настройки игрока ─────────
def enabled(ch, cat: str) -> bool:
    """Включена ли категория у игрока (дефолт — всё включено)."""
    return bool(ch.flags.get("notify", {}).get(cat, True))


def set_pref(ch, cat: str, on: bool):
    ch.flags.setdefault("notify", {})[cat] = bool(on)


def toggle_pref(ch, cat: str) -> bool:
    """Переключить категорию; вернуть новое состояние."""
    new = not enabled(ch, cat)
    set_pref(ch, cat, new)
    return new


# ───────── персональный лимит пушей (пресеты) ─────────
LIMIT_PRESETS = (1, 2, 5)
DEFAULT_LIMIT = 2


def limit(ch) -> int:
    """Персональный суточный лимит push игрока: пресет из ch.flags["notify"]["limit"],
    дефолт DEFAULT_LIMIT. Значение вне LIMIT_PRESETS (повреждённые данные) —
    откатываемся на дефолт, а не падаем."""
    val = ch.flags.get("notify", {}).get("limit", DEFAULT_LIMIT)
    try:
        val = int(val)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return val if val in LIMIT_PRESETS else DEFAULT_LIMIT


def cycle_limit(ch) -> int:
    """Переключить лимит по кругу 1→2→5→1; вернуть новое значение."""
    cur = limit(ch)
    idx = LIMIT_PRESETS.index(cur) if cur in LIMIT_PRESETS else 0
    new = LIMIT_PRESETS[(idx + 1) % len(LIMIT_PRESETS)]
    ch.flags.setdefault("notify", {})["limit"] = new
    return new


# ───────── тумблер тихих часов ─────────
def quiet_off(ch) -> bool:
    """Игрок отключил тихие часы (готов получать push и ночью)."""
    return bool(ch.flags.get("notify", {}).get("quiet_off", False))


def set_quiet_off(ch, off: bool):
    ch.flags.setdefault("notify", {})["quiet_off"] = bool(off)


def toggle_quiet_off(ch) -> bool:
    """Переключить тумблер тихих часов; вернуть новое состояние."""
    new = not quiet_off(ch)
    set_quiet_off(ch, new)
    return new


# ───────── часовой пояс игрока ─────────
def tz_offset(ch=None) -> int:
    """Смещение UTC игрока (часы). Читаем ch.flags["notify"]["tz_offset"], клампим
    в [TZ_MIN, TZ_MAX]; при отсутствии/мусоре — серверный дефолт DEFAULT_TZ (+3)."""
    if ch is None:
        return DEFAULT_TZ
    val = ch.flags.get("notify", {}).get("tz_offset", DEFAULT_TZ)
    try:
        val = int(val)
    except (TypeError, ValueError):
        return DEFAULT_TZ
    return val if TZ_MIN <= val <= TZ_MAX else DEFAULT_TZ


def set_tz_offset(ch, offset: int):
    """Записать часовой пояс игрока (кламп в допустимый диапазон)."""
    try:
        offset = int(offset)
    except (TypeError, ValueError):
        offset = DEFAULT_TZ
    offset = max(TZ_MIN, min(TZ_MAX, offset))
    ch.flags.setdefault("notify", {})["tz_offset"] = offset
    return offset


# ───────── тихие часы (чистые функции) ─────────
def _hour(now: float, ch=None) -> int:
    """Локальный час игрока: UTC-час + его смещение (mod 24). Не зависит от TZ
    сервера — в этом и смысл (см. tz_offset). Для ch=None берётся дефолт +3."""
    utc_h = datetime.fromtimestamp(now, tz=timezone.utc).hour
    return (utc_h + tz_offset(ch)) % 24


def is_quiet(now: float, ch=None) -> bool:
    """Тихие часы 23:00–09:00 по ЛОКАЛЬНОМУ времени игрока (UTC + его tz_offset).
    Если tz не задан (или ch=None) — серверный дефолт +3 (МСК).
    Если у персонажа включён тумблер quiet_off — тихих часов для него нет."""
    if ch is not None and quiet_off(ch):
        return False
    h = _hour(now, ch)
    return h >= QUIET_START or h < QUIET_END


def next_morning(now: float) -> float:
    """Ближайшие 09:00 (>= now)."""
    dt = datetime.fromtimestamp(now)
    morning = dt.replace(hour=QUIET_END, minute=0, second=0, microsecond=0)
    ts = morning.timestamp()
    if ts <= now:
        ts += 86400
    return ts


# ───────── квота (чистые функции) ─────────
def _today(now: float) -> str:
    return datetime.fromtimestamp(now).strftime("%Y-%m-%d")


def quota_left(ch, now: float) -> int:
    lim = limit(ch)
    q = ch.flags.get("notify_quota") or {}
    if q.get("date") != _today(now):
        return lim
    return max(0, lim - int(q.get("count", 0)))


def _quota_bump(ch, now: float):
    """Учесть один отправленный push (со сбросом счётчика по дате)."""
    today = _today(now)
    q = ch.flags.get("notify_quota") or {}
    if q.get("date") != today:
        q = {"date": today, "count": 0}
    q["count"] = int(q.get("count", 0)) + 1
    ch.flags["notify_quota"] = q


def record_sent(ch, cat: str, now: float):
    """Учесть фактически отправленный push в суточной квоте игрока.

    Нужно для рассылок broadcast_all: там политика проверяется через allow()
    (читает квоту), но САМ счётчик надо двигать явно — иначе мировые пуши
    (world_event/world_boss/…) не расходуют лимит и он не соблюдается. Сделки
    (_OFF_QUOTA) вне лимита — их не считаем (как и в due())."""
    if cat in _OFF_QUOTA:
        return
    _quota_bump(ch, now)


def allow(ch, cat: str, now: float) -> str:
    """Решение по политике для одной записи (чистое, без сайд-эффектов).
    -> "send" | "drop" | "defer" (defer => отложить до 09:00)."""
    if not enabled(ch, cat):
        return "drop"
    if is_quiet(now, ch):
        if cat in _QUIET_DROP:
            return "drop"
        if cat in _QUIET_DEFER:
            return "defer"
        # прочее (сезон/гильдия/сделки) в тихие часы — молча ждёт до утра
        if cat not in _OFF_QUOTA:
            return "defer"
    if cat not in _OFF_QUOTA and quota_left(ch, now) <= 0:
        return "drop"
    return "send"


# ───────── очередь ─────────
def emit(uid: int, category: str, text: str, fire_at: float = None):
    """Положить уведомление в очередь (fire_at — не раньше этого времени)."""
    if not ENABLED:
        return
    _QUEUE.append({"uid": uid, "category": category, "text": text,
                   "fire_at": fire_at or 0.0})


def emit_broadcast(category: str, text: str, fire_at: float = None):
    """Широковещательное уведомление (uid=None → bot разошлёт всем целям)."""
    if not ENABLED:
        return
    _QUEUE.append({"uid": None, "category": category, "text": text,
                   "fire_at": fire_at or 0.0})


def pending() -> int:
    return len(_QUEUE)


def clear():
    _QUEUE.clear()


def due(now: float, chars: dict = None):
    """Вернуть готовые к отправке записи, применив политику к персональным.

    chars: uid -> Character (для проверки настроек/квоты). Широковещательные
    (uid=None) отдаются как есть — bot применит политику per-uid при рассылке.
    Записи с fire_at в будущем остаются в очереди. Отправленные персональные
    учитываются в квоте. Отложенные (defer) переставляются на 09:00.
    """
    chars = chars or {}
    ready, keep = [], []
    for rec in _QUEUE:
        if rec["fire_at"] > now:
            keep.append(rec)
            continue
        if rec["uid"] is None:            # широковещалка — политика на стороне bot
            ready.append(rec)
            continue
        ch = chars.get(rec["uid"])
        if ch is None:                    # оффлайн: bot решит по БД сам
            ready.append(rec)
            continue
        verdict = allow(ch, rec["category"], now)
        if verdict == "send":
            _quota_bump(ch, now)
            ready.append(rec)
        elif verdict == "defer":
            rec["fire_at"] = next_morning(now)
            keep.append(rec)
        # drop — просто выбрасываем
    _QUEUE[:] = keep
    return ready
