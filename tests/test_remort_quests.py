# -*- coding: utf-8 -*-
"""Veteran quest chain: factual NPC memory, gates, scaling and repeat safety."""
from ai import npc_ai
from engine.character import Character, LEVEL_CAP
from engine.content import QUESTS, validate
from engine import quest


def hero():
    ch = Character(uid=1042, name="Вернувшийся", cls="warrior")
    ch.init_skills()
    ch.init_vitals()
    return ch


def test_newcomer_cannot_take_veteran_quests():
    ch = hero()
    assert "remort_witness" not in quest.available_quests(ch, "старейшина")
    ok, _ = quest.accept(ch, "remort_witness")
    assert not ok and "remort_witness" not in ch.quests
    ch.level = LEVEL_CAP
    ch.quests["main_arrival"] = "done"
    ch.flags["quest_choices"] = {"sample_choose_faith": "light"}
    assert ch.remort()
    assert ch.quests["main_arrival"] == "done"
    assert ch.flags["quest_choices"]["sample_choose_faith"] == "light"
    return ch


def finish_witness(ch):
    assert "remort_witness" in quest.available_quests(ch, "старейшина")
    assert quest.accept(ch, "remort_witness")[0]
    assert quest.on_talk(ch, "наставник")
    assert quest.is_complete(ch, "remort_witness")
    before_gold = ch.gold
    assert quest.complete(ch, "remort_witness")[0]
    assert ch.gold > before_gold
    assert not quest.complete(ch, "remort_witness")[0]


def test_chain_and_history():
    ch = test_newcomer_cannot_take_veteran_quests()
    context = npc_ai._quest_context(ch, "старейшина")
    assert "круга перерождения №1" in context
    assert QUESTS["main_arrival"]["name"] in context
    assert QUESTS["remort_witness"]["name"] in context
    assert "Память о прежнем пути" in npc_ai._system_prompt(
        {"name": "Хальдер", "role": "старейшина"}, quests=context)

    # Прямой callback не должен обходить ни цепочку, ни ограничение уровня.
    assert not quest.accept(ch, "remort_pack")[0]
    finish_witness(ch)
    assert not quest.accept(ch, "remort_pack")[0]
    ch.level = 4
    assert quest.required_kills(ch, "remort_pack") == 2
    assert quest.accept(ch, "remort_pack")[0]
    assert "0/2" in quest.journal(ch)
    assert not quest.is_complete(ch, "remort_pack")
    assert quest.on_kill(ch, "вожак_стаи")
    assert not quest.is_complete(ch, "remort_pack")
    assert quest.on_kill(ch, "вожак_стаи")
    assert quest.is_complete(ch, "remort_pack")
    assert quest.on_kill(ch, "вожак_стаи") == []
    assert quest.complete(ch, "remort_pack")[0]
    assert not quest.accept(ch, "remort_pack")[0]

    # Второй круг: старый сюжет и выбор сохраняются; ветка ветерана
    # становится доступна вновь, а предыдущая победа входит в память NPC.
    ch.level = LEVEL_CAP
    assert ch.remort()
    assert ch.remort_count == 2
    assert ch.quests["main_arrival"] == "done"
    assert ch.flags["quest_choices"]["sample_choose_faith"] == "light"
    assert "remort_witness" not in ch.quests and "remort_pack" not in ch.quests
    assert "remort_pack:kills" not in ch.quests
    assert ch.flags["remort_quest_history"] == {"remort_witness": 1,
                                                "remort_pack": 1}
    assert quest.required_kills(ch, "remort_pack") == 3
    assert "прежних кругов" in npc_ai._quest_context(ch, "старейшина")
    finish_witness(ch)
    ch.level = 4
    assert quest.accept(ch, "remort_pack")[0]
    assert "0/3" in quest.journal(ch)
    for _ in range(2):
        quest.on_kill(ch, "вожак_стаи")
    assert not quest.is_complete(ch, "remort_pack")
    quest.on_kill(ch, "вожак_стаи")
    assert quest.is_complete(ch, "remort_pack")


def test_content_and_late_quest_gate():
    validate()
    for qid in ("remort_witness", "remort_pack", "remort_knight",
                "remort_nightmare", "remort_reaper"):
        assert QUESTS[qid]["repeat_on_remort"] is True
        assert QUESTS[qid]["min_remort"] == 1
    ch = hero()
    ch.level = LEVEL_CAP
    assert ch.remort()
    # Ни уровень 22, ни сам факт перерождения не обходят цепочку.
    ch.level = 22
    assert not quest.accept(ch, "remort_reaper")[0]
    ch.quests["remort_nightmare"] = "done"
    assert quest.accept(ch, "remort_reaper")[0]
    assert quest.required_kills(ch, "remort_reaper") == 6


def test_full_veteran_chain():
    ch = test_newcomer_cannot_take_veteran_quests()
    finish_witness(ch)
    for qid in ("remort_pack", "remort_knight", "remort_nightmare",
                "remort_reaper"):
        q = QUESTS[qid]
        ch.level = q["min_level"]
        assert qid in quest.available_quests(ch, q["giver"]), qid
        assert quest.accept(ch, qid)[0], qid
        for _ in range(quest.required_kills(ch, qid)):
            quest.on_kill(ch, q["objective"]["mob"])
        assert quest.is_complete(ch, qid), qid
        assert quest.complete(ch, qid)[0], qid
        assert not quest.complete(ch, qid)[0], qid


if __name__ == "__main__":
    test_newcomer_cannot_take_veteran_quests()
    test_chain_and_history()
    test_content_and_late_quest_gate()
    test_full_veteran_chain()
    print("OK: veteran quests, NPC history, strict gates, scaling and per-cycle rewards")
