# -*- coding: utf-8 -*-
"""
Бог-оркестратор мира: LLM периодически смотрит на СВОДКУ мира и предлагает
запустить одно мировое событие из СТРОГО валидируемого каталога (data/events.yaml).

Принцип: движок — арбитр, ИИ — советник. Ни одно невалидное решение LLM не
исполняется: id обязан быть из каталога, зона — из allowed_zones события,
длительность клампится в границы, текст-анонс экранируется и обрезается. При
любой ошибке/выключенном ИИ — деградация на fallback (случайное валидное
событие + шаблонный анонс из name/desc). Бюджет: не чаще одного обращения к
LLM в GOD_MIN_INTERVAL, независимо от того, как часто зовёт воркер.

Экспортирует:
  build_summary(chars, world)   компактная строка-сводка (≤600 симв.)
  decide(chars, world)          -> dict {event_id, zone, duration, announce, source}
  epic_chronicle(prev_season)   -> str|None: летопись минувшего сезона (LLM/шаблон)
"""
import json
import random
import re
import time
from typing import Optional

from engine import events as _events
from engine import seasons as _seasons
from engine import chronicle as _chronicle
from engine import log as _log
from . import provider

_logger = _log.get("ai.god")

# Версия промпта бога-оркестратора (Этап 8). ПОЛИТИКА: меняешь _SYSTEM или
# _EPIC_SYSTEM — инкрементируй версию ("god-v2" -> "god-v3"). Уходит в журнал
# ai/llmlog с каждым вызовом (context="god" для decide, "epic" для летописи).
PROMPT_VERSION = "god-v2"


# Жёсткий бюджет: минимальный интервал между РЕАЛЬНЫМИ обращениями к LLM (сек).
# Даже если god_worker тикает чаще — decide() между вызовами уходит в fallback.
GOD_MIN_INTERVAL = 3600

# Границы длины анонса и «летописи» (символы) — беречь токены и экран игрока.
_ANNOUNCE_MAX = 220
_EPIC_MAX = 900
_SUMMARY_MAX = 600

# время последнего фактического вызова LLM в decide() (0 = ещё не звали)
_last_llm_call = 0.0

# символы, ломающие Markdown Telegram — вырезаем из текста модели (анти-инъекция)
_MD_INJECT = re.compile(r"[*_`\[\]]")


def reset_budget():
    """Сбросить бюджетный таймер (для тестов)."""
    global _last_llm_call
    _last_llm_call = 0.0


def _esc(text: str, limit: int = _ANNOUNCE_MAX) -> str:
    """Обеззаразить текст модели: убрать markdown-инъекции, схлопнуть пробелы,
    обрезать до limit символов. Возвращает чистую строку (возможно пустую)."""
    if not text:
        return ""
    t = _MD_INJECT.sub("", str(text))
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > limit:
        t = t[:limit - 1].rstrip() + "…"
    return t


# ───────────────────────── СВОДКА МИРА ─────────────────────────
def _online_stats(chars: dict):
    """(онлайн, всего, средний_уровень_онлайна). Онлайн = есть свежая активность.

    Считаем «онлайн» по last_seen за последние 10 минут, если атрибут есть;
    иначе — все переданные chars (деградация на пустом/тестовом мире)."""
    total = len(chars or {})
    if not chars:
        return 0, 0, 0
    now = time.time()
    online = []
    for c in chars.values():
        seen = getattr(c, "last_seen", None)
        if seen is None or (now - float(seen)) <= 600:
            online.append(c)
    if not online:
        online = list(chars.values())
    lvls = [int(getattr(c, "level", 1) or 1) for c in online]
    avg = round(sum(lvls) / len(lvls)) if lvls else 0
    return len(online), total, avg


def build_summary(chars: dict, world) -> str:
    """Компактная сводка мира для промпта бога (≤600 симв.).

    Не падает на пустом мире/None. Содержит: онлайн/всего, средний уровень
    онлайна, последние записи хроники, активные события, номер сезона."""
    online, total, avg = _online_stats(chars or {})
    parts = [f"Игроки: онлайн {online}/{total}, средний уровень онлайна ~{avg}."]

    try:
        sid = _seasons.season_id()
    except Exception:
        sid = 0
    parts.append(f"Сезон: {sid}.")

    # активные события
    act = _events.active() if hasattr(_events, "active") else []
    if act:
        names = ", ".join(e["def"].get("name", "?") for e in act)
        parts.append(f"Сейчас идёт: {names}.")
    else:
        parts.append("Мировых событий сейчас нет.")

    # хроника (5 свежих)
    recent = _chronicle.recent(5) if hasattr(_chronicle, "recent") else []
    if recent:
        parts.append("Недавно: " + "; ".join(recent) + ".")

    summary = " ".join(parts)
    if len(summary) > _SUMMARY_MAX:
        summary = summary[:_SUMMARY_MAX - 1].rstrip() + "…"
    return summary


def _catalog_brief() -> str:
    """Краткое описание доступных событий из каталога (id · суть · зоны)."""
    lines = []
    for eid, d in _events._DEFS.items():
        spec = d.get("allowed_zones")
        if spec is None:
            zones = "везде" if d.get("zone") is None else str(d.get("zone"))
        elif spec == "any":
            zones = "везде"
        elif spec == "city":
            zones = "город"
        elif spec == "wild":
            zones = "дикие земли"
        elif isinstance(spec, (list, tuple)):
            zones = ", ".join(str(z) for z in spec)
        else:
            zones = str(spec)
        # границы длительности
        dmin = d.get("duration_min", d.get("duration", 1800))
        dmax = d.get("duration_max", d.get("duration", 1800))
        desc = _esc(d.get("desc", ""), 80)
        lines.append(f'- {eid}: {desc} (зоны: {zones}; длительность {dmin}-{dmax}с)')
    return "\n".join(lines)


def _catalog_zone_hint(eid: str) -> str:
    """Подсказка для zone в JSON: конкретная зона, "city"/"wild"/null."""
    d = _events._DEFS.get(eid, {})
    spec = d.get("allowed_zones")
    if spec in ("city", "wild"):
        return f'"{spec}" или конкретная зона из этого типа'
    if d.get("zone") is None and (spec is None or spec == "any"):
        return "null (эффект глобальный)"
    return "конкретная зона из списка"


# ───────────────────────── ВАЛИДАЦИЯ РЕШЕНИЯ ─────────────────────────
def _resolve_zone_token(eid: str, zone):
    """Привести zone-токен из ответа LLM к тому, что понимает events.start().

    LLM может прислать конкретную зону, "city"/"wild"/"any", null или мусор.
    Возвращаем (zone_for_start, ok): ok=False → зона невалидна для события."""
    d = _events._DEFS.get(eid, {})
    # null / пусто → глобально (если событие это допускает)
    if zone in (None, "", "null", "any"):
        # для событий с фиксированной зоной или city/wild — пусть start() подберёт
        if d.get("zone") is None and d.get("allowed_zones") in (None, "any"):
            return None, True
        return None, False if d.get("allowed_zones") in ("city", "wild") else True
    if isinstance(zone, str):
        # тип-токены — не конкретная зона; отдаём None, чтобы start() выбрал сам
        if zone in ("city", "wild"):
            spec = d.get("allowed_zones")
            return None, (spec == zone)
        # конкретная зона: валидна ли для события?
        if _events.zone_allowed(d, zone):
            return zone, True
        return None, False
    return None, False


def _validate_decision(obj: dict):
    """Проверить решение LLM. -> (clean_dict|None, error|None).

    clean_dict = {event_id, zone, duration, announce}. error!=None — почему
    отвергли (для повторной попытки/лога)."""
    if not isinstance(obj, dict):
        return None, "ответ не является объектом JSON"
    eid = obj.get("event_id")
    if eid not in _events._DEFS:
        return None, f"event_id '{eid}' не из каталога"
    zone_raw = obj.get("zone")
    zone, ok = _resolve_zone_token(eid, zone_raw)
    if not ok:
        return None, f"зона '{zone_raw}' недопустима для события '{eid}'"
    # длительность: клампим средствами движка (не отвергаем — движок арбитр)
    dur = obj.get("duration_sec")
    duration = _events._clamp_duration(_events._DEFS[eid], dur)
    announce = _esc(obj.get("announce", ""))
    if not announce:
        return None, "announce пуст после очистки"
    return {"event_id": eid, "zone": zone, "duration": duration,
            "announce": announce}, None


def _parse_json(raw: str):
    """Достать JSON-объект из ответа модели (модель могла обрамить текстом)."""
    if not raw:
        return None
    # прямой парс
    try:
        return json.loads(raw)
    except Exception:
        pass
    # выкусить первый {...} блок
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


# ───────────────────────── FALLBACK ─────────────────────────
def _template_announce(d: dict) -> str:
    """Шаблонный анонс без ИИ из name/desc события (уже очищенный/обрезанный)."""
    name = _esc(d.get("name", "Событие"), 60)
    desc = _esc(d.get("desc", ""), 150)
    return _esc(f"{name}. {desc}".strip().rstrip("."), _ANNOUNCE_MAX)


def fallback_decision(rng=None) -> dict:
    """Детерминируемый (при переданном rng) fallback: случайное валидное событие
    из каталога + шаблонный анонс. Всегда возвращает валидный dict."""
    rng = rng or random
    eid = rng.choice(list(_events._DEFS))
    d = _events._DEFS[eid]
    duration = _events._clamp_duration(d, None)
    return {
        "event_id": eid,
        "zone": None,             # None → start() сам подберёт валидную зону
        "duration": duration,
        "announce": _template_announce(d),
        "source": "fallback",
    }


# ───────────────────────── РЕШЕНИЕ ─────────────────────────
_SYSTEM = (
    "Ты — бог туманного мира тёмного фэнтези «Семь Корон». Королевство душит "
    "Туман — дыхание вернувшегося Падшего Короля. Ты изредка вмешиваешься в мир, "
    "запуская ОДНО событие из каталога, чтобы оживить его для смертных. "
    "Ты — советник: выбирай событие, уместное текущему положению мира. "
    "Ответь СТРОГО одним JSON-объектом без пояснений и без markdown:\n"
    '{"event_id": "<id из каталога>", "zone": <null | "city" | "wild" | конкретная зона>, '
    '"duration_sec": <целое в границах события>, '
    '"announce": "<1-2 предложения в духе тёмного фэнтези, без спецсимволов>"}'
)


async def decide(chars: dict, world, rng=None, now=None) -> dict:
    """Решение бога о следующем мировом событии.

    Если provider.enabled() и прошёл бюджетный интервал — спросить LLM, строго
    провалидировать; при невалидном ответе — одна повторная попытка с сообщением
    об ошибке; затем fallback. Без ИИ/ошибка/бюджет — fallback.
    Возвращает {event_id, zone, duration, announce, source: "llm"|"fallback"}."""
    global _last_llm_call
    rng = rng or random
    now = now or time.time()

    # Бюджет + доступность ИИ: иначе сразу fallback.
    if not provider.enabled() or (now - _last_llm_call) < GOD_MIN_INTERVAL:
        return fallback_decision(rng)

    _last_llm_call = now
    summary = build_summary(chars, world)
    catalog = _catalog_brief()
    user = (f"Положение мира:\n{summary}\n\n"
            f"Доступные события (каталог):\n{catalog}\n\n"
            "Выбери одно событие и верни JSON.")

    last_err = None
    for attempt in range(2):     # исходная попытка + одна повторная
        messages = [{"role": "user", "content": user}]
        if last_err:
            messages.append({"role": "user",
                             "content": f"Твой прошлый ответ отвергнут: {last_err}. "
                                        "Верни ИСПРАВЛЕННЫЙ строгий JSON."})
        try:
            raw = await provider.chat(_SYSTEM, messages, tier="mid",
                                      max_tokens=260, temperature=0.8,
                                      context="god", version=PROMPT_VERSION)
        except Exception as e:
            _log.log_err(_logger, "god_decide_llm_failed", e, attempt=attempt)
            raw = None
        obj = _parse_json(raw)
        clean, err = _validate_decision(obj) if obj is not None else (None, "не удалось разобрать JSON")
        if clean:
            clean["source"] = "llm"
            return clean
        last_err = err

    # обе попытки неудачны → деградация
    return fallback_decision(rng)


# ───────────────────────── ЛЕТОПИСЬ СЕЗОНА ─────────────────────────
_EPIC_SYSTEM = (
    "Ты — летописец туманного мира тёмного фэнтези «Семь Корон». Заверши минувший "
    "сезон эпической записью в трёх коротких абзацах: помяни героев и павших, "
    "судьбы фракций, поступь Тумана. Пиши мрачно и торжественно, без markdown и "
    "без списков. Не более трёх абзацев."
)


def _template_epic(records: list, prev_season: int) -> str:
    """Шаблонная летопись без ИИ из записей хроники. Непуста при непустой хронике."""
    head = f"🕯 Сезон {prev_season} завершён."
    if not records:
        return _esc(head + " Туман сомкнулся над тихими землями — ни песен, ни имён "
                    "не осталось в памяти этого круга.", _EPIC_MAX)
    body = " ".join(f"— {r}" for r in records[:8])
    tail = "Так минул сезон; Туман ждёт следующих."
    return _esc(f"{head} Молва сохранила: {body} {tail}", _EPIC_MAX)


async def epic_chronicle(prev_season: int) -> Optional[str]:
    """Написать летопись минувшего сезона prev_season (LLM или шаблон).

    Берёт до 20 последних записей хроники. С ИИ — 3 абзаца ≤900 симв.; без ИИ
    или при ошибке — шаблонная сводка из тех же записей. Возвращает None только
    если хроника пуста И ИИ недоступен... нет — всегда что-то возвращает
    (шаблон непуст даже на пустой хронике). Может вернуть None лишь при явном
    сбое очистки."""
    records = _chronicle.recent(20) if hasattr(_chronicle, "recent") else []

    if provider.enabled():
        joined = "; ".join(records) if records else "(записей нет)"
        user = (f"Минувший сезон: {prev_season}. Хроника событий сезона:\n{joined}\n\n"
                "Напиши летопись этого сезона.")
        try:
            raw = await provider.chat(_EPIC_SYSTEM, [{"role": "user", "content": user}],
                                      tier="mid", max_tokens=400, temperature=0.85,
                                      context="epic", version=PROMPT_VERSION)
        except Exception as e:
            _log.log_err(_logger, "god_epic_llm_failed", e, season=prev_season)
            raw = None
        if raw:
            # экранируем инъекции, но СОХРАНЯЕМ переносы абзацев
            cleaned = _MD_INJECT.sub("", raw).strip()
            cleaned = re.sub(r"[ \t]+", " ", cleaned)
            cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
            if len(cleaned) > _EPIC_MAX:
                cleaned = cleaned[:_EPIC_MAX - 1].rstrip() + "…"
            if cleaned:
                return cleaned

    # fallback
    return _template_epic(records, prev_season)
