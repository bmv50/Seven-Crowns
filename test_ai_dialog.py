# -*- coding: utf-8 -*-
"""Тесты диалоговых веток: персистентная память NPC + квест-контекст.
Провайдер LLM мокается (DeepSeek недоступен из песочницы). Запуск: python test_ai_dialog.py"""
import asyncio
from ai import cost, npc_ai, provider
from engine.character import Character


def _ch():
    c = Character(uid=777, name="Аск", cls="warrior", race="human")
    c.init_vitals()
    c.quests = {}
    return c


def test_summarize_history():
    hist = [
        {"role": "user", "content": "Игрок (Аск, уровень 5) говорит тебе: «где найти ключ?»"},
        {"role": "assistant", "content": "Ключ у стражи."},
        {"role": "user", "content": "Игрок (Аск, уровень 5) говорит тебе: «а про туман?»"},
    ]
    s = cost.summarize_history(hist)
    assert "ключ" in s.lower() and "туман" in s.lower()
    assert cost.summarize_history([]) == ""
    print("✓ summarize_history (standalone)")


def test_quest_context():
    ch = _ch()
    # у старейшины есть доступные квесты
    qc = npc_ai._quest_context(ch, "старейшина")
    assert "предложить" in qc and len(qc) > 0
    print("✓ quest-контекст для промпта:", qc[:60], "...")


def test_system_prompt_blocks():
    ctx = {"name": "Хальдер", "role": "questgiver", "persona": "Старый.", "knowledge": []}
    p = npc_ai._system_prompt(ctx, memory="игрок искал ключ",
                              quests="ты можешь предложить ему: Волчья напасть")
    assert "помнишь: игрок искал ключ" in p
    assert "Состояние заданий" in p and "Волчья напасть" in p
    print("✓ system-промпт содержит память и квест-блок")


def test_persistent_memory_across_sessions():
    # мок-провайдер: «включён» и возвращает канонический ответ
    orig_enabled, orig_chat = provider.enabled, provider.chat
    provider.enabled = lambda: True
    async def fake_chat(system, messages, tier="mid", **kw):
        fake_chat.last_system = system
        return "И тебе привет, странник."
    provider.chat = fake_chat
    try:
        npc = "старейшина"
        ch = _ch()
        npc_ai.reset(ch.uid)
        cost.SESSIONS.reset(ch.uid)
        t = 1000.0
        # разговор
        asyncio.run(npc_ai.say_action(ch, npc, "где ключ от подвала", now=t))
        # пауза > таймаута → история должна сжаться в ch.flags["npc_mem"]
        asyncio.run(npc_ai.say_action(ch, npc, "привет снова", now=t + 99999))
        mem = ch.flags.get("npc_mem", {}).get(npc)
        assert mem and "ключ" in mem.lower(), f"память не записана: {mem}"
        print("✓ память записана в ch.flags (переживёт рестарт):", mem[:50])

        # НОВАЯ сессия (чистые in-process структуры), память берётся из ch.flags
        npc_ai.reset(ch.uid)
        cost.SESSIONS.reset(ch.uid)
        cost.CACHE.clear()
        asyncio.run(npc_ai.say_action(ch, npc, "ну что там по туману", now=t + 200000))
        assert "помнишь:" in fake_chat.last_system and "ключ" in fake_chat.last_system.lower()
        print("✓ в новой сессии NPC помнит игрока (память из ch.flags в промпте)")
    finally:
        provider.enabled, provider.chat = orig_enabled, orig_chat


if __name__ == "__main__":
    test_summarize_history()
    test_quest_context()
    test_system_prompt_blocks()
    test_persistent_memory_across_sessions()
    print("\n=== ai dialog OK ===")
