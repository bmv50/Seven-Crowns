# -*- coding: utf-8 -*-
"""Тесты контроля стоимости LLM и парсинга действий. Запуск: python test_ai_cost.py"""
from ai import cost, actions


def test_jaccard_and_cache():
    c = cost.SemanticCache()
    c.put("kuznec", "где купить меч", "Кузница за углом, странник.")
    assert c.get("kuznec", "где купить меч сейчас", threshold=0.5) is not None
    assert c.get("kuznec", "расскажи о тумане глубин подробно", threshold=0.9) is None
    assert c.get("mag", "где купить меч", threshold=0.5) is None
    print("✓ semantic cache (lexical)")


def test_cache_ttl_and_isolation():
    import os
    os.environ["AI_CACHE_TTL"] = "100"
    c = cost.SemanticCache()
    c.put("5:kuznec", "где меч", "ответ A", now=1000.0)
    assert c.get("5:kuznec", "где меч", threshold=0.5, now=1050.0) == "ответ A"
    assert c.get("5:kuznec", "где меч", threshold=0.5, now=1200.0) is None
    assert c.get("9:kuznec", "где меч", threshold=0.5, now=1050.0) is None
    del os.environ["AI_CACHE_TTL"]
    print("✓ cache TTL + изоляция по игроку (ns=uid:npc)")


def test_bucket_sweep_no_leak():
    b = cost.TokenBucket()
    base = b._day() * 86400
    for i in range(5100):
        b.record(i, "npc", now=base - 86400 + 10)
    b.record(99999, "npc", now=base + 10)
    assert len(b._counts) == 1 and (99999, "npc") in b._counts
    print("✓ token bucket: прунинг старых дней (нет утечки)")


def test_token_bucket_daily_limit():
    import os
    os.environ["AI_DAILY_PER_NPC"] = "3"
    b = cost.TokenBucket()
    uid, npc = 1, "elrik"
    for _ in range(3):
        assert b.allow(uid, npc)
        b.record(uid, npc)
    assert not b.allow(uid, npc)
    assert b.used(uid, npc) == 3
    tomorrow = (b._day() + 1) * 86400 + 10
    assert b.allow(uid, npc, now=tomorrow)
    del os.environ["AI_DAILY_PER_NPC"]
    print("✓ token bucket (суточный лимит + сброс)")


def test_dialogue_timeout_and_summary():
    import os
    os.environ["AI_DIALOG_TIMEOUT"] = "300"
    s = cost.DialogueSession()
    uid, npc = 7, "strannik"
    s.touch(uid, npc, now=1000)
    assert not s.expired(uid, npc, now=1200)
    assert s.expired(uid, npc, now=1400)
    hist = [
        {"role": "user", "content": "Игрок (Макс, уровень 5) говорит тебе: «где найти ключ?»"},
        {"role": "assistant", "content": "Ключ у стражи."},
        {"role": "user", "content": "Игрок (Макс, уровень 5) говорит тебе: «а туман откуда?»"},
    ]
    s.summarize(uid, npc, hist)
    summ = s.summary(uid, npc)
    assert summ and "ключ" in summ.lower() and "туман" in summ.lower()
    del os.environ["AI_DIALOG_TIMEOUT"]
    print("✓ dialogue timeout + локальное сжатие в воспоминание")


def test_action_parse():
    text, act = actions.parse('Бери, странник. {"action":"offer_quest","quest_id":"echo_1"}')
    assert act and act["action"] == "offer_quest" and act["quest_id"] == "echo_1"
    assert "Бери" in text and "{" not in text
    t2, a2 = actions.parse("Просто реплика без действия.")
    assert a2 is None and t2 == "Просто реплика без действия."
    t3, a3 = actions.parse("Текст {не json}")
    assert a3 is None
    print("✓ парсинг хвостового JSON-действия")


def test_action_validate_rejects_fake():
    class Ch:
        uid = 1
        flags = {}
        level = 5
    ch = Ch()
    bad = {"action": "offer_quest", "quest_id": "__несуществующий__"}
    assert actions.validate(bad, ch, "kuznec_torgar", {}) is None
    assert actions.validate({"action": "nuke_city"}, ch, "kuznec_torgar", {}) is None
    assert actions.validate({"action": "to_vendor"}, ch, "x", {"role": "guard"}) is None
    assert actions.validate({"action": "to_vendor"}, ch, "x", {"role": "vendor"}) == {"action": "to_vendor"}
    print("✓ валидация отсекает выдуманные действия")


if __name__ == "__main__":
    test_jaccard_and_cache()
    test_cache_ttl_and_isolation()
    test_bucket_sweep_no_leak()
    test_token_bucket_daily_limit()
    test_dialogue_timeout_and_summary()
    test_action_parse()
    test_action_validate_rejects_fake()
    print("\n=== ai cost/actions OK ===")
