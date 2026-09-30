"""Newcomer PvP protection, surrender semantics and persisted duel vitals."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from engine import karma, money
from engine.character import Character
from engine.social import DuelManager
from test_bot_interaction import load_functions


def hero(uid, level=1, remort=0):
    ch = Character(uid=uid, name=f"Герой{uid}", cls="warrior", room="field", level=level)
    ch.flags["remort"] = remort
    ch.init_vitals()
    return ch


def test_newcomer_protection_is_symmetric():
    rookie, veteran = hero(1), hero(2, level=25)
    assert karma.newcomer_protected(rookie)
    assert not karma.open_pvp_allowed(veteran, rookie)
    assert not karma.open_pvp_allowed(rookie, veteran)
    rookie.level = karma.SOFT_DEATH_LEVEL
    assert karma.open_pvp_allowed(rookie, veteran)
    rookie.level = 1
    rookie.flags["remort"] = 1
    assert karma.open_pvp_allowed(rookie, veteran)


def test_death_drops_only_unequipped_copies():
    ch = hero(3, level=10)
    ch.flags["karma"] = 100
    ch.inventory = ["меч", "меч"]
    ch.equipment["weapon"] = "меч"
    with patch("engine.karma.random.random", return_value=0), \
         patch("engine.karma.random.choice", side_effect=lambda items: items[0]):
        assert karma.maybe_drop_on_death(ch) == "меч"
        assert ch.inventory == ["меч"] and ch.equipment["weapon"] == "меч"
        assert karma.maybe_drop_on_death(ch) is None


async def test_callback_cannot_bypass_newcomer_protection():
    attacker, rookie = hero(1, level=25), hero(2)
    chars = {1: attacker, 2: rookie}
    dm = DuelManager()
    cb = SimpleNamespace(answer=AsyncMock())
    start = AsyncMock()
    env = load_functions({"pvp_attack"}, {
        "chars": chars, "WORLD": {"field": {"safe": False}},
        "_presence": SimpleNamespace(active=lambda uid: True),
        "world": SimpleNamespace(living_in=lambda room: []),
        "duel_mgr": dm, "bot": SimpleNamespace(send_message=AsyncMock()),
        "start_duel": start,
    })
    await env["pvp_attack"](cb, attacker, 2)
    assert not dm.duels and not dm.requests
    start.assert_not_awaited()
    assert cb.answer.call_args.kwargs.get("show_alert") is True
    await env["pvp_attack"](cb, attacker, 1)
    assert not dm.duels
    rookie.level = karma.SOFT_DEATH_LEVEL
    await env["pvp_attack"](cb, attacker, 2)
    assert dm.in_duel(1) and dm.in_duel(2)
    start.assert_awaited_once_with(1, 2)


async def test_surrender_is_not_kill_and_restored_vitals_are_saved():
    winner, loser = hero(1, level=10), hero(2, level=10)
    winner.gold, loser.gold = 100, 1000
    winner.hp, loser.hp = 2, 1
    dm = DuelManager()
    dm.challenge(1, 2)
    dm.accept(2)
    for state in dm.duels.values():
        state["open"] = True
    snapshots = []

    async def save(ch, force=False):
        snapshots.append((ch.uid, ch.hp, ch.mp, ch.gold, force))

    env = load_functions({"end_duel"}, {
        "duel_mgr": dm, "WORLD": {"field": {"safe": False}},
        "arena": SimpleNamespace(update=Mock()), "save": save, "money": money,
        "chars": {1: winner, 2: loser}, "duel_view": {},
        "bot": SimpleNamespace(send_message=AsyncMock(), edit_message_text=AsyncMock()),
        "ui": SimpleNamespace(render_room=lambda *a: "Комната", kb_room=lambda *a: None),
        "world": object(), "others_in": lambda room: [], "ITEMS": {},
    })
    with patch("engine.karma.on_pvp_kill") as pk, patch("engine.karma.maybe_drop_on_death") as drop:
        await env["end_duel"](winner, loser, ["Сдача"], killed=False)
    pk.assert_not_called()
    drop.assert_not_called()
    assert (winner.gold, loser.gold) == (200, 900)
    assert not dm.duels
    assert snapshots == [(1, winner.max_hp, winner.start_resource(), 200, True),
                         (2, loser.max_hp, loser.start_resource(), 900, True)]


async def test_yield_handler_marks_nonlethal_and_missing_opponent_cancels():
    ch, opp = hero(1, level=10), hero(2, level=10)
    dm = DuelManager()
    dm.challenge(1, 2)
    dm.accept(2)
    end = AsyncMock()
    cb = SimpleNamespace(answer=AsyncMock())
    env = load_functions({"duel_yield"}, {
        "duel_mgr": dm, "chars": {1: ch, 2: opp}, "end_duel": end,
    })
    await env["duel_yield"](cb, ch)
    assert end.await_args.kwargs == {"killed": False}
    assert end.await_args.args[:2] == (opp, ch)

    dm.end(1)
    dm.challenge(1, 2)
    dm.accept(2)
    ch.hp = 1
    saved = AsyncMock()
    edited = AsyncMock()
    env = load_functions({"duel_attack"}, {
        "duel_mgr": dm, "chars": {1: ch}, "duel_view": {1: {"id": 1}},
        "save": saved, "safe_edit": edited,
        "ui": SimpleNamespace(render_room=lambda *a: "Комната", kb_room=lambda *a: None),
        "world": object(), "others_in": lambda room: [],
    })
    await env["duel_attack"](cb, ch)
    assert not dm.duels and ch.hp == ch.max_hp
    saved.assert_awaited_once_with(ch)
    edited.assert_awaited_once()

    dm.challenge(1, 2)
    dm.accept(2)
    ch.hp = 1
    env = load_functions({"duel_yield"}, {
        "duel_mgr": dm, "chars": {1: ch}, "duel_view": {},
        "save": saved, "safe_edit": edited,
        "ui": SimpleNamespace(render_room=lambda *a: "Комната", kb_room=lambda *a: None),
        "world": object(), "others_in": lambda room: [],
    })
    await env["duel_yield"](cb, ch)
    assert not dm.duels and ch.hp == ch.max_hp
    assert saved.await_count == 2 and edited.await_count == 2


if __name__ == "__main__":
    test_newcomer_protection_is_symmetric()
    test_death_drops_only_unequipped_copies()
    asyncio.run(test_callback_cannot_bypass_newcomer_protection())
    asyncio.run(test_surrender_is_not_kill_and_restored_vitals_are_saved())
    asyncio.run(test_yield_handler_marks_nonlethal_and_missing_opponent_cancels())
    print("OK: first-run PvP guard, surrender and durable duel vitals")
