# -*- coding: utf-8 -*-
"""
Живые реплики NPC через ИИ. Собирает промпт из персоны/роли/фракции/знаний
(engine.npc.ai_context) и состояния игрока, держит короткую историю диалога.
Откатывается на None (→ шаблон) при выключенном ИИ или ошибке.

Экономия LLM (адаптация из референса TeleMud, см. ai/cost.py):
  • семантический (лексический) кэш ответов по вопросу;
  • суточный лимит обращений на пару (игрок, NPC);
  • тайм-аут диалога со сжатием истории в «воспоминание».
Действия NPC из ответа модели (offer_quest и т.п.) валидируются в ai/actions.py.
"""
import time
from typing import Dict, List, Optional, Tuple, Union

from engine import npc as npclib
from engine import chronicle
from . import provider
from . import cost
from . import actions
from . import memory as npc_memory
from . import textguard

# Версия промпта NPC (Этап 8). ПОЛИТИКА: меняешь текст системного промпта
# (WORLD_CANON / _system_prompt / rules) — ОБЯЗАТЕЛЬНО инкрементируй версию
# ("npc-v3" -> "npc-v4"). Версия уходит в журнал ai/llmlog вместе с каждым
# вызовом, чтобы по логам было видно, какой промпт породил какой исход/стоимость.
PROMPT_VERSION = "npc-v3"

# короткая память диалога: (uid, npc_id) -> [{role, content}, ...]
_history: Dict[Tuple[int, str], List[dict]] = {}
_MAX_TURNS = 8   # хранить последние 8 реплик (4 обмена)

# Канон мира — общий контекст для всех NPC (кратко, чтобы не жечь токены).
WORLD_CANON = (
    "Мир: тёмное фэнтези «Семь Корон». Королевство Аэльдмар душит Туман — "
    "дыхание вернувшегося Падшего Короля; где туман густеет, мёртвые встают, а живое "
    "искажается. Истинный источник тумана — в Глуби под руинами. Есть фракции: "
    "Орден Рассвета (выжечь туман), Вольные рудокопы (нажиться), Шепчущий круг "
    "(равновесие), Ковен Гнилотопи (договор с мёртвыми), Воинство Короля (нежить), "
    "Вольные торговцы (нейтралы)."
)


def _quest_context(ch, npc_id: str) -> str:
    """Краткая сводка заданий для промпта, чтобы NPC «вёл» квест репликами."""
    try:
        from engine import quest
        from engine.content import QUESTS
    except Exception:
        return ""
    bits = []
    av = quest.available_quests(ch, npc_id)
    if av:
        bits.append("ты можешь предложить ему: " + ", ".join(QUESTS[q]["name"] for q in av))
    ti = quest.turn_in_quests(ch, npc_id)
    if ti:
        bits.append("он может сдать тебе: " + ", ".join(QUESTS[q]["name"] for q in ti))
    act = quest.active_brief(ch)
    if act:
        bits.append("сейчас он занят: " + "; ".join(a.replace("🎯 ", "") for a in act[:2]))
    return " · ".join(bits)


def _choice_context(ch) -> str:
    """Краткая строка о сделанных игроком выборах в квестах (для ИИ-промпта).
    Пустая, если выборов нет — не жжём токены."""
    choices = (getattr(ch, "flags", {}) or {}).get("quest_choices") or {}
    if not choices:
        return ""
    try:
        from engine.content import QUESTS
    except Exception:
        return ""
    labels = []
    for qid, opt_id in choices.items():
        obj = (QUESTS.get(qid) or {}).get("objective", {})
        for o in (obj.get("options") or []):
            if o.get("id") == opt_id:
                labels.append(o.get("label", opt_id))
                break
    if not labels:
        return ""
    return "Игрок сделал выбор: " + ", ".join(labels) + "."


def _chronicle_context() -> str:
    """Блок «недавние события мира» для промпта NPC (сплетни). Пустая строка,
    если хроника пуста — не жжём токены на пустой заголовок."""
    events = chronicle.recent(3)
    if not events:
        return ""
    bullet_lines = "\n".join(f"- {e}" for e in events)
    return f"\nНедавние события мира (можешь сплетничать о них):\n{bullet_lines}\n"


def _system_prompt(ctx: dict, memory: Optional[Union[str, List[str]]] = None,
                   action_hint: str = "", quests: str = "", choices: str = "") -> str:
    facts = []
    if ctx.get("faction_stance"):
        facts.append(f"Твоя фракция стоит за: {ctx['faction_stance']}.")
    if ctx.get("knowledge"):
        facts.append("Ты осведомлён о: " + ", ".join(ctx["knowledge"]) + ".")
    if isinstance(memory, (list, tuple)):
        # новая долгая память (ai/memory.py) — список отдельных воспоминаний,
        # маркированные короткие строки, а не одна склеенная строка
        items = [m for m in memory if m]
        if items:
            bullets = "\n".join(f"— {m}" for m in items)
            mem = f"\nИз прошлых бесед с этим игроком ты помнишь:\n{bullets}\n"
        else:
            mem = "\n"
    else:
        # легаси: одна строка (старый ch.flags['npc_mem']) — обратная совместимость
        mem = f"\nИз прошлых бесед с этим игроком ты помнишь: {memory}.\n" if memory else "\n"
    qline = f"Состояние заданий: {quests}. Можешь ненавязчиво подтолкнуть к ним.\n" if quests else ""
    if choices:
        qline += choices + " Можешь учесть это в реплике.\n"
    rules = (
        "Правила: отвечай ТОЛЬКО как этот персонаж, от первого лица, на русском, "
        "1–3 коротких предложения, живо и в характере. Не выходи из роли, не упоминай, "
        "что ты ИИ или игра, не используй современный сленг. Не выдумывай задания, "
        "награды и предметы — об этом игрок узнаёт через игровые кнопки. Можешь "
        "поддержать беседу, дать слух или совет в духе мира."
    )
    if action_hint:
        rules += "\n" + action_hint
    return (
        WORLD_CANON + "\n" + _chronicle_context() + "\n"
        f"Ты играешь персонажа по имени {ctx.get('name')} — это {ctx.get('role')}. "
        f"{ctx.get('persona', '')}\n" + " ".join(facts) + mem + qline + "\n" + rules
    )


def _player_brief(ch, player_text: Optional[str]) -> str:
    who = f"Игрок ({getattr(ch, 'name', 'странник')}, уровень {getattr(ch, 'level', 1)})"
    if player_text:
        return f"{who} говорит тебе: «{player_text}»"
    return f"{who} подходит к тебе. Поприветствуй его коротко, в характере."


async def say_action(ch, npc_id: str,
                     player_text: Optional[str] = None,
                     now: float = None) -> Tuple[Optional[str], Optional[dict]]:
    """
    Реплика NPC + опциональное валидированное игровое действие.
    Возвращает (text|None, action|None). text=None → вызывающий берёт шаблон.
    action — словарь вроде {"action":"offer_quest","quest_id":...}, уже сверенный
    с игровым состоянием (бот может показать кнопку); None — действия нет.
    """
    ctx = npclib.ai_context(npc_id)
    if not ctx or ctx.get("tier") == "none":
        return None, None
    if not provider.enabled():
        return None, None

    now = now or time.time()
    uid = getattr(ch, "uid", 0)
    key = (uid, npc_id)

    # тайм-аут диалога: сжать старую историю в воспоминание, чтобы NPC помнил
    # игрока между сессиями. Легаси-строка ch.flags['npc_mem'] пишется как и
    # раньше (обратная совместимость, старые чтения не ломаем), а новая долгая
    # память (Postgres npc_memories + fallback ch.flags['npc_mem2']) копится
    # через ai/memory.py — именно её умная выборка используется в промпте.
    if cost.SESSIONS.expired(uid, npc_id, now):
        new_sum = cost.summarize_history(_history.get(key, []))
        if new_sum:
            mem_store = ch.flags.setdefault("npc_mem", {})
            prev = mem_store.get(npc_id)
            mem_store[npc_id] = new_sum if not prev else (prev + " | " + new_sum)[-300:]
            await npc_memory.store(ch, npc_id, new_sum)
        _history.pop(key, None)
    cost.SESSIONS.touch(uid, npc_id, now)

    # пространство кэша — на (игрок, NPC), чтобы реплики с личной памятью/уровнем
    # НЕ протекали другим игрокам
    cache_ns = f"{uid}:{npc_id}"
    cache_key = player_text or "__greet__"
    cached = cost.CACHE.get(cache_ns, cache_key, now=now)
    if cached is not None:
        return cached, None

    # суточный лимит на пару (игрок, NPC) — экономим бюджет
    if not cost.BUCKET.allow(uid, npc_id, now):
        return None, None

    hist = _history.setdefault(key, [])
    user_msg = {"role": "user", "content": _player_brief(ch, player_text)}
    hint = actions.available_hint(ch, npc_id, ctx)
    # долгая память NPC об игроке: умная выборка (свежесть+похожесть на реплику)
    # из БД либо fallback в ch.flags — см. ai/memory.py
    mems = await npc_memory.retrieve(ch, npc_id, player_text, k=3, now=now)
    quests = _quest_context(ch, npc_id)
    choices = _choice_context(ch)
    system = _system_prompt(ctx, memory=mems, action_hint=hint, quests=quests, choices=choices)

    raw = await provider.chat(system, hist + [user_msg], tier=ctx["tier"],
                              context="npc", version=PROMPT_VERSION)
    if not raw:
        return None, None                 # ошибка/таймаут — квоту НЕ тратим
    cost.BUCKET.record(uid, npc_id, now)   # считаем только успешные обращения

    text, act = actions.parse(raw)
    act = actions.validate(act, ch, npc_id, ctx)
    if not text:
        text = raw  # на случай, если был только JSON — отдадим как есть
    # Этап 8: единый обеззараживатель текста модели перед показом игроку —
    # вырез markdown-инъекций/управляющих символов + обрезка длины (реплика
    # NPC — единственный из 4 путей LLM-текста, где раньше был только esc_md
    # на стороне вставки; god.announce/epic/errand чистятся у себя).
    text = textguard.sanitize_out(text, max_len=300)

    hist.append(user_msg)
    hist.append({"role": "assistant", "content": text})
    if len(hist) > _MAX_TURNS:
        del hist[:len(hist) - _MAX_TURNS]
    cost.CACHE.put(cache_ns, cache_key, text, now=now)
    return text, act


async def say(ch, npc_id: str, player_text: Optional[str] = None) -> Optional[str]:
    """Обратносовместимая обёртка: только текст реплики (без действия)."""
    text, _action = await say_action(ch, npc_id, player_text)
    return text


def reset(uid: int, npc_id: str = None):
    """Сбросить историю диалога (при уходе из комнаты/смене собеседника)."""
    if npc_id is None:
        for k in [k for k in _history if k[0] == uid]:
            _history.pop(k, None)
    else:
        _history.pop((uid, npc_id), None)
    cost.SESSIONS.reset(uid, npc_id)
