# -*- coding: utf-8 -*-
"""
Игровые действия из ответа LLM (адаптация из референса TeleMud).

Модель может в КОНЦЕ реплики вернуть JSON-объект вида
    {"action": "offer_quest", "quest_id": "echo_1"}
Движок вырезает его из текста и ВАЛИДИРУЕТ по белому списку доступных действий
для данного NPC и игрока. Невалидное/выдуманное действие игнорируется —
LLM не может выдать то, чего нет (квест выдаётся только если он реально доступен
у этого NPC по геймплейным условиям).

Действие НЕ исполняется автоматически: возвращается боту как подсказка/кнопка.
"""
import json
import re
from typing import List, Optional, Tuple

from engine import quest
from engine import errands

ALLOWED = {"offer_quest", "offer_errand", "to_vendor", "to_trainer", "none"}

# markdown-инъекции в озвучке поручения от модели
_MD_INJECT = re.compile(r"[*_`\[\]]")
_ERRAND_TEXT_MAX = 200


def _clean_errand_text(text) -> str:
    """Обеззаразить озвучку поручения: убрать markdown, схлопнуть пробелы, обрезать."""
    if not text:
        return ""
    t = _MD_INJECT.sub("", str(text))
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > _ERRAND_TEXT_MAX:
        t = t[:_ERRAND_TEXT_MAX - 1].rstrip() + "…"
    return t

# JSON-объект в конце строки (нежадно, последняя фигурная группа)
_JSON_TAIL = re.compile(r"\{[^{}]*\}\s*$")


def parse(reply: str) -> Tuple[str, Optional[dict]]:
    """Разделить текст реплики и хвостовой JSON-экшен. Вернуть (text, action|None)."""
    if not reply:
        return reply, None
    m = _JSON_TAIL.search(reply.strip())
    if not m:
        return reply.strip(), None
    raw = m.group(0)
    try:
        obj = json.loads(raw)
    except Exception:
        return reply.strip(), None
    if not isinstance(obj, dict) or "action" not in obj:
        return reply.strip(), None
    text = reply.strip()[:m.start()].strip()
    return text, obj


def validate(action: Optional[dict], ch, npc_id: str, ctx: dict) -> Optional[dict]:
    """
    Пропустить действие, только если оно реально доступно. Иначе вернуть None.
    Не доверяем модели — сверяем с игровым состоянием.
    """
    if not action:
        return None
    name = str(action.get("action", "")).strip()
    if name not in ALLOWED or name == "none":
        return None

    if name == "offer_quest":
        qid = str(action.get("quest_id", "")).strip()
        available = quest.available_quests(ch, npc_id)
        if qid and qid in available:
            return {"action": "offer_quest", "quest_id": qid}
        return None

    if name == "offer_errand":
        # предлагать можно только при отсутствии активного и в пределах лимита
        if not errands.can_offer(ch, npc_id):
            return None
        cands = errands.candidates(ch, npc_id)
        try:
            idx = int(action.get("idx"))
        except (TypeError, ValueError):
            return None
        if not (0 <= idx < len(cands)):
            return None
        text = _clean_errand_text(action.get("text", ""))
        return {"action": "offer_errand", "idx": idx, "text": text}

    role = (ctx or {}).get("role")
    if name == "to_vendor" and role == "vendor":
        return {"action": "to_vendor"}
    if name == "to_trainer" and role in ("trainer", "mentor"):
        return {"action": "to_trainer"}
    return None


def available_hint(ch, npc_id: str, ctx: dict) -> str:
    """
    Короткая подсказка для system-промпта: какие действия модель ВПРАВЕ предложить.
    Если нечего — пустая строка (тогда модель действий не выдаёт).
    """
    opts: List[str] = []
    quests = quest.available_quests(ch, npc_id)
    if quests:
        opts.append('предложить задание: {"action":"offer_quest","quest_id":"'
                    + quests[0] + '"}')
    if errands.can_offer(ch, npc_id):
        n = len(errands.candidates(ch, npc_id))
        opts.append('предложить разовое поручение (idx — номер дела от 0 до '
                    + str(n - 1) + ', text — твоя короткая живая формулировка): '
                    '{"action":"offer_errand","idx":0,"text":"..."}')
    role = (ctx or {}).get("role")
    if role == "vendor":
        opts.append('направить к товарам: {"action":"to_vendor"}')
    if role in ("trainer", "mentor"):
        opts.append('направить к обучению: {"action":"to_trainer"}')
    if not opts:
        return ""
    return ("Если это уместно по ходу беседы, ты МОЖЕШЬ в самом конце ответа "
            "добавить ровно один JSON одной из форм — " + "; ".join(opts) +
            ". Если не нужно — не добавляй ничего. Не придумывай других действий "
            "и других id.")
