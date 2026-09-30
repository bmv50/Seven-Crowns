"""Smoke-test the MAX application path without Telegram or network access."""

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from bot import commands as cmds
from bot.max_transport import MaxInput
from engine import content, starter, textsafe
from engine.character import Character, START_ROOM
from engine.interaction import Presence
from engine.lifecycle_errors import ActiveCharacterExists, NameTaken


def load_handler(env):
    path = Path(__file__).resolve().parents[1] / "bot" / "main.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                and n.name == "_max_handle_input")
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), env)
    return env["_max_handle_input"]


async def test_create_and_move():
    messages = []
    async def send(uid, value):
        messages.append((uid, value))
    async def move(ch, direction):
        ch.room = content.WORLD[ch.room]["exits"][direction]
        return True, False
    database = SimpleNamespace(pool=object(), reserve_max_player_id=AsyncMock(return_value=-11),
                               create_character=AsyncMock())
    analytics = SimpleNamespace(track=Mock(), track_once=Mock())
    env = {
        "asyncio": asyncio, "MaxInput": MaxInput, "db": database,
        "_max_input_locks": {}, "_presence": Presence(), "cmds": cmds,
        "chars": {}, "send": send, "RACES": content.RACES,
        "CLASSES": content.CLASSES, "WORLD": content.WORLD,
        "world": object(),
        "Character": Character, "_ts": textsafe, "_starter": starter,
        "HUB_ROOM": START_ROOM, "NameTaken": NameTaken,
        "ActiveCharacterExists": ActiveCharacterExists,
        "analytics": analytics, "ui": SimpleNamespace(render_room=lambda *_: "ROOM",
             render_stats=lambda *_: "STATS", DIR_ICONS={"север": "↑"}),
        "others_in": lambda *_: [],
        "move_core": move, "quest": SimpleNamespace(on_enter_room=lambda *_: []),
        "weekly": SimpleNamespace(on_room_visit=lambda *_: None),
        "send_tutorial": AsyncMock(), "save": AsyncMock(),
    }
    handler = load_handler(env)
    await handler(MaxInput("42", "start:42:1", "/start"))
    assert "MAX" in messages[-1][1] or "Семь Корон" in messages[-1][1]
    await handler(MaxInput("42", "message:42:1", "/create human warrior Тестер"))
    ch = env["chars"][-11]
    assert ch.room == START_ROOM and ch.name == "Тестер"
    database.create_character.assert_awaited_once()
    await handler(MaxInput("42", "message:42:2", "север"))
    assert ch.room == content.WORLD[START_ROOM]["exits"]["север"]
    await handler(MaxInput("42", "message:42:3", "/stats"))
    assert messages[-1] == (-11, "STATS")
    assert env["_presence"].active(-11)


if __name__ == "__main__":
    asyncio.run(test_create_and_move())
    print("OK: MAX creation, shared movement and character view")
