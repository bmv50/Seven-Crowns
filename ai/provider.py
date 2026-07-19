# -*- coding: utf-8 -*-
"""
Абстракция ИИ-провайдера. Сейчас поддержан DeepSeek (OpenAI-совместимый API).
Игра полноценна без ИИ: если провайдер не настроен/выключен/бюджет исчерпан —
chat() возвращает None, и вызывающий код откатывается на шаблонные реплики.

Конфигурация через окружение (.env):
    AI_PROVIDER       = none | deepseek
    DEEPSEEK_API_KEY  = sk-...
    DEEPSEEK_BASE_URL = https://api.deepseek.com   (по умолчанию)
    DEEPSEEK_MODEL    = deepseek-chat              (по умолчанию)

Этап 8 (укрепление ИИ-слоя):
  • enabled() учитывает дневной HARD-бюджет (ai/cost.py:BUDGET_GUARD) — при
    исчерпании АВАРИЙНО отключает LLM до конца суток; проверка кэшируется на 60с,
    чтобы не дёргать состояние на каждый вызов (и лог-warning не чаще раза в час);
  • runtime kill-switch: set_runtime(False/True/None) — ручной тумблер из /admin
    (env AI_PROVIDER=none по-прежнему выключает жёстко на старте);
  • chat() пишет КАЖДЫЙ реальный вызов в журнал ai/llmlog (токены/латентность/
    стоимость/исход/контекст/версия промпта).
"""
import asyncio
import os
import time
from typing import List, Optional

from engine import log as _log
from . import cost as _cost
from . import llmlog as _llmlog

_logger = _log.get("ai.provider")

# runtime kill-switch: None -> следовать env; True -> вкл (если есть ключ);
# False -> принудительно выкл (тумблер админа). Меняется set_runtime().
_runtime_on: Optional[bool] = None

# кэш результата enabled() — чтобы не считать бюджет/окружение на каждый вызов
_ENABLED_TTL = 60.0
_enabled_cache = {"val": None, "ts": 0.0}

# лог-warning об исчерпанном бюджете — не чаще раза в час
_WARN_INTERVAL = 3600.0
_last_warn = 0.0


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def provider_name() -> str:
    return _env("AI_PROVIDER", "none").lower()


def set_runtime(on: Optional[bool]) -> None:
    """Ручной тумблер ИИ из /admin. None — следовать окружению; True/False —
    жёсткий рантайм-override. Сбрасывает кэш enabled(), чтобы применилось сразу."""
    global _runtime_on, _enabled_cache
    _runtime_on = None if on is None else bool(on)
    _enabled_cache = {"val": None, "ts": 0.0}


def runtime_state() -> Optional[bool]:
    """Текущее состояние рантайм-тумблера (для /admin): None/True/False."""
    return _runtime_on


def _configured() -> bool:
    """Провайдер выбран, есть ключ и не выключен рантайм-тумблером (БЕЗ бюджета)."""
    if _runtime_on is False:
        return False
    if provider_name() == "deepseek":
        return bool(_env("DEEPSEEK_API_KEY"))
    return False


def budget_exhausted(now: float = None) -> bool:
    """Исчерпан ли дневной HARD-бюджет (обёртка над BUDGET_GUARD для /admin)."""
    return _cost.BUDGET_GUARD.exhausted(now)


def _maybe_warn(now: float) -> None:
    global _last_warn
    if now - _last_warn >= _WARN_INTERVAL:
        _last_warn = now
        _logger.warning(
            "llm_budget_exhausted: дневной бюджет %.4f USD исчерпан (расход %.4f) "
            "— ИИ аварийно отключён до конца суток, игра на шаблонах",
            _cost.daily_budget_usd(), _cost.BUDGET_GUARD.spent_today(now))


def enabled(now: float = None) -> bool:
    """Готов ли ИИ к работе: провайдер настроен, тумблер не выключен И дневной
    бюджет не исчерпан. Результат кэшируется на 60с (бюджет/окружение не читаются
    на каждый вызов); set_runtime() сбрасывает кэш немедленно."""
    now = now if now is not None else time.time()
    c = _enabled_cache
    if c["val"] is not None and (now - c["ts"]) < _ENABLED_TTL:
        return c["val"]
    base = _configured()
    over = _cost.BUDGET_GUARD.exhausted(now) if base else False
    if base and over:
        _maybe_warn(now)
    val = base and not over
    c["val"], c["ts"] = val, now
    return val


async def chat(system: str, messages: List[dict], tier: str = "mid",
               max_tokens: int = 220, temperature: float = 0.9,
               context: str = "npc", version: str = "") -> Optional[str]:
    """
    Отправить запрос модели. messages — список {role, content} БЕЗ system.
    Возвращает текст ответа или None при ошибке/таймауте/выключенном ИИ.
    tier (cheap/mid/premium) пока маппится на одну модель — задел на будущее.
    context/version — для журнала ai/llmlog (npc|god|errand|epic + версия промпта).
    """
    if not enabled():
        return None
    if provider_name() == "deepseek":
        return await _deepseek(system, messages, max_tokens, temperature,
                               tier, context, version)
    return None


async def _deepseek(system: str, messages: List[dict],
                    max_tokens: int, temperature: float,
                    tier: str, context: str, version: str) -> Optional[str]:
    # aiohttp идёт зависимостью aiogram — отдельная установка не нужна
    import aiohttp

    base = _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    model = _env("DEEPSEEK_MODEL", "deepseek-chat")
    key = _env("DEEPSEEK_API_KEY")
    url = f"{base}/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}] + messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    t0 = time.time()

    def _log_call(outcome, tin=0, tout=0):
        # каждый реальный вызов -> ровно одна запись журнала (токены/стоимость/исход)
        try:
            _llmlog.record(provider="deepseek", model=model, tier=tier,
                           latency_ms=int((time.time() - t0) * 1000),
                           tokens_in=tin, tokens_out=tout,
                           outcome=outcome, context=context, version=version)
        except Exception:
            pass   # телеметрия LLM никогда не роняет диалог

    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(url, json=payload, headers=headers) as r:
                if r.status != 200:
                    _log_call("error")
                    return None
                data = await r.json()
        usage = data.get("usage") or {}
        tin = int(usage.get("prompt_tokens") or 0)
        tout = int(usage.get("completion_tokens") or 0)
        text = (data["choices"][0]["message"]["content"] or "").strip() or None
        # текст пуст после strip -> модель ответила «ничем»: считаем invalid
        _log_call("ok" if text else "invalid", tin, tout)
        return text
    except asyncio.TimeoutError as e:
        _log.log_err(_logger, "llm_chat_timeout", e, model=model)
        _log_call("timeout")
        return None
    except Exception as e:
        # сеть/формат ответа LLM — не роняем игру (откат на шаблоны),
        # но фиксируем в лог (без ключа/заголовков: только модель и тип ошибки).
        _log.log_err(_logger, "llm_chat_failed", e, model=model)
        _log_call("error")
        return None
