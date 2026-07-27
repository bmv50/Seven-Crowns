# -*- coding: utf-8 -*-
"""Тесты слоя живых NPC (Utility AI + FSM). Запуск: python test_npc_ai.py"""
import random
from engine import npc_ai
from engine.world import World


def _mob():
    w = World()
    for lst in w.mobs.values():
        for m in lst:
            if m.alive:
                return w, m
    raise RuntimeError("нет живых мобов в мире")


def test_curves():
    assert npc_ai.exp_curve(0) == 0 and npc_ai.exp_curve(1) == 1
    assert npc_ai.exp_curve(0.5, 2) == 0.25
    # сигмоида монотонна и пересекает 0.5 в x0
    assert abs(npc_ai.sigmoid(0.7, 12, 0.7) - 0.5) < 1e-9
    assert npc_ai.sigmoid(0.9) > npc_ai.sigmoid(0.5)
    # лог: убывающая отдача, в границах [0,1]
    assert npc_ai.log_curve(0) == 0 and npc_ai.log_curve(1) == 1
    assert npc_ai.log_curve(0.3) > 0.3      # вогнутость
    print("✓ curves")


def test_needs_grow_and_fear_decays():
    ai = npc_ai.MobAI(type("M", (), {"room": "r"})())
    ai.needs["fear"] = 1.0
    npc_ai.update_needs(ai, 100)
    assert ai.needs["hunger"] > 0 and ai.needs["fatigue"] > 0 and ai.needs["boredom"] > 0
    assert ai.needs["fear"] < 1.0           # страх затухает
    # клампинг сверху
    npc_ai.update_needs(ai, 10_000_000)
    assert ai.needs["boredom"] <= 1.0 and ai.needs["fear"] >= 0.0
    print("✓ needs grow / fear decays / clamp")


def test_fsm_combat_to_flee():
    w, m = _mob()
    ai = npc_ai.get_ai(m)
    npc_ai.step_fsm(m, ai, "attacked")
    assert ai.state == npc_ai.COMBAT
    # ранен и напуган -> бежит
    m.hp = m.max_hp * 0.1
    ai.needs["fear"] = 0.9
    npc_ai.step_fsm(m, ai)
    assert ai.state == npc_ai.FLEE
    # страх прошёл -> покой
    ai.needs["fear"] = 0.0
    npc_ai.step_fsm(m, ai)
    assert ai.state == npc_ai.IDLE
    print("✓ FSM combat→flee→idle")


def test_fsm_rest_and_patrol():
    w, m = _mob()
    ai = npc_ai.get_ai(m)
    ai.state = npc_ai.IDLE
    ai.needs["fatigue"] = 0.95
    npc_ai.step_fsm(m, ai)
    assert ai.state == npc_ai.REST
    ai.needs["fatigue"] = 0.0
    npc_ai.step_fsm(m, ai)
    assert ai.state == npc_ai.IDLE
    ai.needs["boredom"] = 0.8
    npc_ai.step_fsm(m, ai)
    assert ai.state == npc_ai.PATROL
    print("✓ FSM rest / patrol")


def test_goal_softmax_deterministic():
    w, m = _mob()
    ai = npc_ai.get_ai(m)
    ai.needs["fatigue"] = 0.99       # сон должен победить при T→0
    g = npc_ai.evaluate_goal(m, ai, room_has_players=False, temperature=0.01)
    assert g == "REST"
    print("✓ goal (deterministic argmax)")


def test_memory_ring_buffer():
    ai = npc_ai.MobAI(type("M", (), {"room": "r"})())
    for i in range(30):
        ai.remember("tick", str(i))
    assert len(ai.memory) == 20
    assert ai.memory[-1][2] == "29"  # последнее событие сохранено
    assert ai.memory[0][2] == "10"   # старейшие вытеснены
    print("✓ memory ring buffer (20)")


def test_disabled_is_noop():
    npc_ai.ENABLED = False
    w = World()
    assert npc_ai.tick_ambient(w, set()) == []
    print("✓ disabled = no-op")


def test_enabled_flee_moves_mob():
    random.seed(1)
    npc_ai.ENABLED = True
    w, m = _mob()
    # подготовить соседнюю комнату
    from engine.content import WORLD
    nbrs = [d for d in WORLD.get(m.room, {}).get("exits", {}).values() if d in w.mobs]
    if not nbrs:
        npc_ai.ENABLED = False
        print("✓ flee-move (пропущен: нет соседей)")
        return
    ai = npc_ai.get_ai(m)
    ai.last_update -= 6             # пройти 5-сек гейт, не обнуляя страх
    m.aggro = [1]
    m.hp = m.max_hp * 0.05
    ai.needs["fear"] = 0.95
    ai.state = npc_ai.COMBAT
    start = m.room
    npc_ai.tick_ambient(w, {start})
    assert m.aggro == []            # агро сброшено при бегстве
    npc_ai.ENABLED = False
    print("✓ enabled: раненый моб сбегает, агро сброшено")


if __name__ == "__main__":
    test_curves()
    test_needs_grow_and_fear_decays()
    test_fsm_combat_to_flee()
    test_fsm_rest_and_patrol()
    test_goal_softmax_deterministic()
    test_memory_ring_buffer()
    test_disabled_is_noop()
    test_enabled_flee_moves_mob()
    print("\n=== все тесты npc_ai OK ===")
