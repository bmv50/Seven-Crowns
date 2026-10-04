"""Shared party guards and Telegram-to-MAX invitations without live APIs."""

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from engine import textsafe, uigate
from engine.character import Character, START_ROOM
from engine.interaction import Presence
from engine.social import PartyManager


def hero(uid, name, level=3):
    ch = Character(uid=uid, name=name, cls="warrior", race="human")
    ch.init_vitals()
    ch.init_skills()
    ch.room = START_ROOM
    ch.level = level
    return ch


def load_party(env):
    path = Path(__file__).resolve().parents[1] / "bot" / "main.py"
    names = {"party_invite_core", "party_accept_core", "party_decline_core",
             "party_leave_core", "party_chat_core", "party_invite"}
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = [node for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    assert len(nodes) == len(names)
    env.update({"Character": Character, "CallbackQuery": object})
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), env)
    return env


async def test_guards_and_cross_transport_invite():
    leader, max_player, third = hero(10, "Лидер"), hero(-20, "Напарник"), hero(30, "Третий")
    presence = Presence()
    presence.touch(leader.uid)
    sent = AsyncMock()
    bot = SimpleNamespace(send_message=AsyncMock())
    env = load_party({"chars": {ch.uid: ch for ch in (leader, max_player, third)},
                      "party_mgr": PartyManager(), "_presence": presence,
                      "_uigate": uigate, "_ts": textsafe, "send": sent, "bot": bot})
    invite = env["party_invite_core"]
    assert not invite(leader, leader.uid)[0]
    assert not invite(leader, max_player.uid)[0]  # ещё не онлайн
    presence.touch(max_player.uid)
    leader.level = 1
    assert not invite(leader, max_player.uid)[0]
    leader.level = 3
    max_player.level = 1
    assert not invite(leader, max_player.uid)[0]
    max_player.level = 3
    max_player.room = "market"
    assert not invite(leader, max_player.uid)[0]
    assert not env["party_mgr"].invites
    max_player.room = START_ROOM
    cb = SimpleNamespace(answer=AsyncMock())
    await env["party_invite"](cb, leader, max_player.uid)
    sent.assert_awaited_once()
    assert sent.await_args.args[0] == max_player.uid
    assert "/paccept" in sent.await_args.args[1]
    bot.send_message.assert_not_awaited()
    assert env["party_mgr"].invites[max_player.uid] == leader.uid
    assert not invite(leader, max_player.uid)[0]  # приглашение не перезаписывается
    party, _ = env["party_accept_core"](max_player)
    assert party and set(party["members"]) == {leader.uid, max_player.uid}
    assert env["party_accept_core"](max_player)[0] is None
    presence.touch(third.uid)
    assert not invite(max_player, third.uid)[0]  # не лидер
    assert invite(leader, third.uid)[0]
    assert env["party_decline_core"](third) == "❌ Приглашение отклонено."
    assert third.uid not in env["party_mgr"].invites
    sent.reset_mock()
    ok, reply = await env["party_chat_core"](max_player, "Привет *герой*")
    assert ok and "Привет" in reply
    assert sent.await_args.args[0] == leader.uid
    assert "\\*герой\\*" in sent.await_args.args[1]
    assert env["party_leave_core"](max_player) == [leader.uid]
    assert env["party_mgr"].party_of(max_player.uid) is None
    assert not (await env["party_chat_core"](leader, "один"))[0]


def test_old_invite_cannot_rejoin_recreated_party():
    mgr = PartyManager()
    assert mgr.invite(10, 20)
    assert mgr.invites[20] == 10
    assert mgr.leave(10) == 10
    assert 20 not in mgr.invites
    assert mgr.invite(10, 30)
    assert mgr.accept(20) is None
    assert mgr.invite(10, 10) is False
    assert mgr.accept(30) is not None
    assert mgr.invite(10, 30) is False


if __name__ == "__main__":
    asyncio.run(test_guards_and_cross_transport_invite())
    test_old_invite_cannot_rejoin_recreated_party()
    print("OK: party proximity, level, leader, cross-transport invite and chat guards")
