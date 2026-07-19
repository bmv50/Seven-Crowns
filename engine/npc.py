# -*- coding: utf-8 -*-
"""
Слой NPC: доступ к данным персонажей, шаблонные реплики и заготовка
контекста под будущий ИИ (persona + faction + knowledge + tier).
Игра работает на шаблонных `lines`; ИИ подключается позже сбоку.
"""
import random
from typing import Optional

from .content import NPCS, FACTIONS

# importance -> модель ИИ по умолчанию (можно переопределить полем ai_tier)
TIER_BY_IMPORTANCE = {
    "ambient": "none",
    "common": "cheap",
    "named": "mid",
    "key": "premium",
}
IMPORTANCE_LEVELS = set(TIER_BY_IMPORTANCE.keys())


def exists(npc_id: str) -> bool:
    return npc_id in NPCS


def get(npc_id: str) -> Optional[dict]:
    return NPCS.get(npc_id)


def display_name(npc_id: str) -> str:
    n = NPCS.get(npc_id)
    return n["name"] if n and n.get("name") else npc_id.replace("_", " ").capitalize()


def emoji(npc_id: str) -> str:
    n = NPCS.get(npc_id) or {}
    return n.get("emoji", "👤")


def ai_tier(npc_id: str) -> str:
    """Какой моделью ИИ обслуживать этого NPC (none/cheap/mid/premium)."""
    n = NPCS.get(npc_id) or {}
    if n.get("ai_tier"):
        return n["ai_tier"]
    return TIER_BY_IMPORTANCE.get(n.get("importance", "ambient"), "none")


def line(npc_id: str) -> str:
    """Шаблонная реплика (до подключения ИИ)."""
    n = NPCS.get(npc_id)
    if not n:
        return "…"
    lines = n.get("lines") or []
    return random.choice(lines) if lines else "…"


def ai_context(npc_id: str) -> dict:
    """
    Заготовка контекста для будущего ИИ-промпта. Сам вызов модели здесь НЕ
    происходит — это данные, которые ИИ-слой соберёт в промпт (см. ROADMAP).
    """
    n = NPCS.get(npc_id) or {}
    fac = n.get("faction")
    return {
        "id": npc_id,
        "name": n.get("name"),
        "role": n.get("role"),
        "importance": n.get("importance", "ambient"),
        "tier": ai_tier(npc_id),
        "faction": fac,
        "faction_stance": (FACTIONS.get(fac, {}) or {}).get("stance"),
        "persona": (n.get("persona") or "").strip(),
        "knowledge": n.get("knowledge", []),
    }


# роль NPC -> человекочитаемая подпись (чтобы было видно, кто кузнец/учитель)
ROLE_LABEL = {
    "vendor": "🛒 Торговец",
    "trainer": "🎓 Учитель навыков",
    "mentor": "🌟 Наставник",
    "banker": "🏦 Банкир",
    "innkeeper": "🛏 Трактирщик",
    "arena_master": "🏟 Мастер арены",
    "guild_master": "⚒ Глава гильдии",
    "questgiver": "❗ Даёт задания",
    "priest": "✨ Жрец",
    "faction_leader": "👑 Глава фракции",
    "guard": "🛡 Стража",
    "gossip": "🗨 Слухи",
    "entity": "🌀 Сущность",
}


def role_label(npc_id: str) -> str:
    r = (NPCS.get(npc_id) or {}).get("role")
    return ROLE_LABEL.get(r, "🧑 Житель")
