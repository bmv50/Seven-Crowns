"""NPC talk and quest reward pipeline is shared by Telegram and MAX."""

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from engine import content, game_actions, quest, reputation
from engine.character import Character, START_ROOM


def load_core(env):
    # Python 3.12 evaluates annotations while exec() compiles extracted nodes;
    # Python 3.14 defers them, so provide the same symbol as bot.main imports.
    env.setdefault("Character", Character)
    path = Path(__file__).resolve().parents[1] / "bot" / "main.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    wanted = {"talk_core", "complete_quest_core", "complete_errand_core"}
    nodes = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name in wanted]
    assert len(nodes) == len(wanted)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), env)
    return env


async def test_npc_talk_progress():
    ch = Character(uid=-201, name="Игрок", cls="warrior", race="human")
    ch.init_vitals()
    ch.room = START_ROOM
    assert game_actions.quest_accept_here(ch, "sample_talk_elder")[0]
    ai = SimpleNamespace(say_action=AsyncMock(return_value=("Привет", None)))
    save = AsyncMock()
    env = load_core({
        "asyncio": asyncio, "WORLD": content.WORLD, "talking_to": {},
        "npc_ai": ai, "_elog": SimpleNamespace(log_err=Mock()), "_log": None,
        "_stash_errand": Mock(), "quest": quest,
        "_events": SimpleNamespace(ENABLED=False), "weekly": None, "save": save,
        "npc_dialog": lambda _ch, _npc, line=None: line,
    })
    body, _act, progress = await env["talk_core"](ch, "жрец_храма")
    assert "не рядом" in body and progress == []
    ai.say_action.assert_not_awaited()
    ch.room = "temple"
    body, _act, progress = await env["talk_core"](ch, "жрец_храма")
    assert body == "Привет" and progress
    assert ch.quests["sample_talk_elder:talk"] == "1"
    save.assert_awaited_once()


async def test_shared_quest_reward_once():
    ch = Character(uid=-202, name="Игрок2", cls="warrior", race="human")
    ch.init_vitals()
    ch.room = START_ROOM
    assert game_actions.quest_accept_here(ch, "sample_reach_well")[0]
    ch.quests["sample_reach_well:reach"] = "1"
    save = AsyncMock()
    env = load_core({
        "game_actions": game_actions, "gl": SimpleNamespace(_check_levelup=AsyncMock()),
        "achievements": SimpleNamespace(check=lambda _ch: []),
        "npclib": __import__("engine.npc", fromlist=["get"]),
        "QUESTS": content.QUESTS, "content": content, "reputation": reputation,
        "analytics": SimpleNamespace(track_once=Mock()),
        "tutorial": SimpleNamespace(on_event=lambda *_: []), "save": save,
    })
    before = ch.gold
    ok, _text = await env["complete_quest_core"](ch, "sample_reach_well")
    assert ok and ch.gold == before + 1500
    ok, _text = await env["complete_quest_core"](ch, "sample_reach_well")
    assert not ok and ch.gold == before + 1500
    assert save.await_count == 2
    save.assert_awaited_with(ch, force=True)


async def test_reward_checkpoint_before_failure():
    from copy import deepcopy
    from engine import errands
    for kind in ('quest', 'errand'):
        ch = Character(uid=-203, name='Сохранение', cls='warrior', race='human')
        ch.init_vitals()
        ch.room = START_ROOM
        initial_gold = ch.gold
        if kind == 'quest':
            ch.quests.update({'sample_reach_well': 'active', 'sample_reach_well:reach': '1'})
        else:
            ch.flags['errand'] = {'npc': 'наставник', 'type': 'kill', 'mob': 'крыса',
                'count': 1, 'progress': 1, 'reward': {'gold': 123, 'xp': 45, 'items': []}}
        persisted = []
        async def save(actor, force=False):
            # A dirty mark alone would not survive an immediate process crash.
            if force:
                persisted.append(deepcopy(actor))
        levels = AsyncMock(side_effect=RuntimeError('later callback failed'))
        env = load_core(dict(game_actions=game_actions, save=save,
            gl=SimpleNamespace(_check_levelup=levels)))
        try:
            if kind == 'quest':
                await env['complete_quest_core'](ch, 'sample_reach_well')
            else:
                await env['complete_errand_core'](ch, 'наставник')
        except RuntimeError:
            pass
        else:
            raise AssertionError('Callback failure not injected')
        assert len(persisted) == 1
        restored = persisted[0]
        assert restored.gold == initial_gold + (1500 if kind == 'quest' else 123)
        assert (restored.quests.get('sample_reach_well') == 'done' if kind == 'quest'
                else not errands.has_active(restored))
        # Unavailable durable storage blocks post-reward callbacks entirely.
        fresh = deepcopy(restored)
        if kind == 'quest':
            fresh.quests.update({'sample_reach_well': 'active', 'sample_reach_well:reach': '1'})
        else:
            fresh.flags['errand'] = {'npc': 'наставник', 'type': 'kill', 'mob': 'крыса',
                'count': 1, 'progress': 1, 'reward': {'gold': 123, 'xp': 45, 'items': []}}
        env['save'] = AsyncMock(side_effect=ConnectionError())
        levels.reset_mock()
        try:
            if kind == 'quest':
                await env['complete_quest_core'](fresh, 'sample_reach_well')
            else:
                await env['complete_errand_core'](fresh, 'наставник')
        except ConnectionError:
            pass
        else:
            raise AssertionError('Checkpoint failure not injected')
        levels.assert_not_awaited()


if __name__ == "__main__":
    asyncio.run(test_npc_talk_progress())
    asyncio.run(test_shared_quest_reward_once())
    asyncio.run(test_reward_checkpoint_before_failure())
    print("OK: shared NPC proximity, talk progress and exactly-once quest reward")
