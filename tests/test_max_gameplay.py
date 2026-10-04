"""Smoke-test the MAX application path without Telegram or network access."""

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from bot import commands as cmds
from bot.max_transport import MaxInput
from engine import content, errands, game_actions, npc, quest, skills, starter, textsafe, money, uigate
from engine.character import Character, START_ROOM
from engine.interaction import Presence
from engine.social import PartyManager
from engine.lifecycle_errors import ActiveCharacterExists, NameTaken


def load_handler(env):
    from engine import max_navigation
    env.setdefault('_max_reply', env['send'])
    env.setdefault('_max_navigation', max_navigation)
    env.setdefault('_max_shop_command', AsyncMock(return_value=False))
    if '_max_context_reply' not in env:
        async def context_reply(ch, text, npc_id=None):
            await env['send'](ch.uid, text)
        env['_max_context_reply'] = context_reply
    path = Path(__file__).resolve().parents[1] / "bot" / "main.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {"_max_handle_input", "_max_npc_commands", "_max_resolve_npc",
             "party_invite_core", "party_accept_core", "party_decline_core",
             "party_leave_core", "party_chat_core"}
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name in names]
    assert len(nodes) == len(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), env)
    return env["_max_handle_input"]


async def test_create_and_move():
    messages = []
    async def send(uid, value, **kwargs):
        messages.append((uid, value))
    async def move(ch, direction):
        ch.room = content.WORLD[ch.room]["exits"][direction]
        return True, False
    database = SimpleNamespace(pool=object(), reserve_max_player_id=AsyncMock(
                                   side_effect=lambda external: -11 if external == "42" else -12),
                               create_character=AsyncMock())
    analytics = SimpleNamespace(track=Mock(), track_once=Mock())
    chosen_vendor = {}
    ui = SimpleNamespace(render_room=lambda *_: "ROOM", render_stats=lambda *_: "STATS",
                         render_skills=lambda *_: "SKILLS",
                         DIR_ICONS={"север": "↑", "юг": "↓"}, active_vendor=chosen_vendor,
                         current_vendor=lambda ch: chosen_vendor.get(ch.uid)
                         or (game_actions.vendors_here(ch) or [None])[0])
    characters = {}
    env = {
        "asyncio": asyncio, "MaxInput": MaxInput, "db": database,
        "_max_input_locks": {}, "_presence": Presence(), "cmds": cmds,
        "chars": characters, "send": send, "RACES": content.RACES,
        "CLASSES": content.CLASSES, "WORLD": content.WORLD,
        "world": object(),
        "Character": Character, "_ts": textsafe, "_starter": starter,
        "HUB_ROOM": START_ROOM, "NameTaken": NameTaken,
        "ActiveCharacterExists": ActiveCharacterExists,
        "analytics": analytics, "ui": ui, "game_actions": game_actions,
        "_max_guild_command": AsyncMock(return_value=False),
        "_max_auction_command": AsyncMock(return_value=False),
        "_max_preferences_command": AsyncMock(return_value=False),
        "_uigate": uigate, "party_mgr": PartyManager(),
        "players_in_room": lambda actor: [other for other in characters.values()
                            if other.uid != actor.uid and other.room == actor.room],
        "render_group": lambda actor: f"GROUP {actor.name}",
        "_mod": SimpleNamespace(is_banned=lambda _uid: False,
                                is_muted=lambda _uid: False,
                                chat_allowed=lambda _uid: True),
        "quest": quest, "errands": errands, "skillmod": skills,
        "QUESTS": content.QUESTS, "ITEMS": content.ITEMS, "SKILLS": content.SKILLS,
        "npclib": npc, "money": money, "_max_choice_pending": {},
        "talk_core": AsyncMock(return_value=("NPC_DIALOG", None, [])),
        "complete_quest_core": AsyncMock(return_value=(True, "QUEST_DONE")),
        "complete_errand_core": AsyncMock(return_value=(True, "ERRAND_DONE")),
        "others_in": lambda *_: [],
        "move_core": move,
        "weekly": SimpleNamespace(on_room_visit=lambda *_: None),
        "send_tutorial": AsyncMock(), "save": AsyncMock(),
    }
    async def mutate_party(operation, unavailable, required_uids=()):
        return operation(env["party_mgr"])
    env["_party_mutate"] = mutate_party
    handler = load_handler(env)
    await handler(MaxInput("42", "start:42:1", "/start"))
    assert "MAX" in messages[-1][1] or "Семь Корон" in messages[-1][1]
    await handler(MaxInput("42", "message:42:1", "/create human warrior Тестер"))
    ch = env["chars"][-11]
    assert ch.room == START_ROOM and ch.name == "Тестер"
    database.create_character.assert_awaited_once()
    await handler(MaxInput("42", "message:42:talk", "/talk наставник"))
    assert "NPC_DIALOG" in messages[-1][1] and "/accept" in messages[-1][1]
    await handler(MaxInput("42", "message:42:accept", "/accept посвящение_новичка"))
    assert ch.quests["посвящение_новичка"] == "active"
    await handler(MaxInput("42", "message:42:2", "север"))
    assert ch.room == content.WORLD[START_ROOM]["exits"]["север"]
    await handler(MaxInput("42", "message:42:remote", "/accept sample_reach_well"))
    assert "sample_reach_well" not in ch.quests
    ch.gold = 5000
    await handler(MaxInput("42", "message:42:shop", "/shop лавочник_туманного_брода"))
    assert "/buy малое_зелье" in messages[-1][1]
    # Buying is covered by the dedicated durable confirmation tests; seed loot
    # through the shared rules here to continue testing the sell path.
    assert game_actions.shop_buy_here(ch, "малое_зелье", "лавочник_туманного_брода")[0]
    assert "малое_зелье" in ch.inventory
    await handler(MaxInput("42", "message:42:sell-list", "/sell"))
    assert "/sell малое_зелье" in messages[-1][1]
    potions_before = ch.inventory.count("малое_зелье")
    await handler(MaxInput("42", "message:42:sell", "/sell малое_зелье"))
    assert "Продано" in messages[-1][1]
    assert ch.inventory.count("малое_зелье") == potions_before - 1
    gold = ch.gold
    env["_mod"].is_banned = lambda _uid: True
    await handler(MaxInput("42", "message:42:banned", "/buy малое_зелье"))
    assert ch.gold == gold and "Доступ ограничен" in messages[-1][1]
    env["_mod"].is_banned = lambda _uid: False
    await handler(MaxInput("42", "message:42:turnin", "/turnin sample_reach_well"))
    assert messages[-1] == (-11, "QUEST_DONE")
    await handler(MaxInput("42", "message:42:badshop", "/shop кузнец"))
    assert "нет" in messages[-1][1].lower()
    await handler(MaxInput("42", "message:42:return", "юг"))
    await handler(MaxInput("42", "message:42:errand", "/errand наставник"))
    assert "Напишите /erraccept" in messages[-1][1]
    await handler(MaxInput("42", "message:42:erraccept", "/erraccept"))
    assert errands.has_active(ch)
    await handler(MaxInput("42", "message:42:errturnin", "/errturnin"))
    assert messages[-1] == (-11, "ERRAND_DONE")
    ch.room = "mine_entrance"
    ch.equipment["weapon"] = "железный_меч"
    ch.set_durab("weapon", 50)
    await handler(MaxInput("42", "message:42:repair-preview", "/repair"))
    assert "/repair confirm" in messages[-1][1]
    before_repair = ch.repair_cost()
    await handler(MaxInput("42", "message:42:repair-confirm", "/repair confirm"))
    assert "починено" in messages[-1][1] and ch.repair_cost() == 0
    assert ch.gold >= 0 and before_repair > 0
    ch.level = skills.learn_level("whirlwind")
    ch.gold = skills.learn_cost("whirlwind")
    await handler(MaxInput("42", "message:42:remote-learn", "/learn whirlwind"))
    assert "whirlwind" not in ch.learned and ch.gold == skills.learn_cost("whirlwind")
    ch.room = "trainers_hall"
    await handler(MaxInput("42", "message:42:train", "/train"))
    assert "/learn whirlwind" in messages[-1][1]
    await handler(MaxInput("42", "message:42:learn", "/learn whirlwind"))
    assert "Изучено" in messages[-1][1] and "whirlwind" in ch.learned and ch.gold == 0
    await handler(MaxInput("42", "message:42:learn-again", "/learn whirlwind"))
    assert ch.gold == 0 and ch.learned.count("whirlwind") == 1
    await handler(MaxInput("42", "message:42:skills", "/skills"))
    assert "/loadout" in messages[-1][1] and "whirlwind" in messages[-1][1]
    await handler(MaxInput("42", "message:42:loadout", "/loadout whirlwind"))
    assert "whirlwind" not in ch.loadout
    await handler(MaxInput("42", "message:42:preset-save", "/preset save 1"))
    assert ch.flags["presets"]["1"] == ch.loadout
    await handler(MaxInput("42", "message:42:loadout-again", "/loadout whirlwind"))
    assert "whirlwind" in ch.loadout
    await handler(MaxInput("42", "message:42:preset-load", "/preset load 1"))
    assert "whirlwind" not in ch.loadout
    ch.loadout.clear()
    await handler(MaxInput("42", "message:42:preset-empty", "/preset save 2"))
    ch.loadout.append("whirlwind")
    await handler(MaxInput("42", "message:42:preset-empty-load", "/preset load 2"))
    assert ch.loadout == [] and "загружен" in messages[-1][1]
    await handler(MaxInput("42", "message:42:invalid-preset", "/preset save 99"))
    assert "Формат" in messages[-1][1] and "99" not in ch.flags["presets"]
    ch.room = START_ROOM
    await handler(MaxInput("42", "message:42:temple", "юг"))
    await handler(MaxInput("42", "message:42:faith", "/accept sample_choose_faith"))
    assert ch.quests["sample_choose_faith"] == "active"
    await handler(MaxInput("42", "message:42:choice", "/choose sample_choose_faith light"))
    assert "sample_faith" not in ch.flags
    assert "quest_choices" not in ch.flags
    await handler(MaxInput("42", "message:42:confirm", "/confirm"))
    assert ch.flags["quest_choices"]["sample_choose_faith"] == "light"
    await handler(MaxInput("42", "message:42:3", "/stats"))
    assert messages[-1] == (-11, "STATS")
    assert env["_presence"].active(-11)
    await handler(MaxInput("43", "message:43:start", "/start"))
    await handler(MaxInput("43", "message:43:create", "/create human warrior Напарник"))
    mate = characters[-12]
    ch.room = mate.room = START_ROOM
    ch.level = mate.level = 3
    await handler(MaxInput("42", "message:42:group", "/group"))
    assert "/pinvite -12" in messages[-1][1]
    await handler(MaxInput("42", "message:42:pinvite", "/pinvite -12"))
    assert env["party_mgr"].invites[-12] == -11
    assert messages[-2][0] == -12 and "/paccept" in messages[-2][1]
    await handler(MaxInput("43", "message:43:accept", "/paccept"))
    assert set(env["party_mgr"].members(-11)) == {-11, -12}
    await handler(MaxInput("42", "message:42:party-chat", "/party Привет, напарник!"))
    assert any(uid == -12 and "Привет, напарник!" in msg for uid, msg in messages)
    await handler(MaxInput("43", "message:43:leave", "/pleave"))
    assert env["party_mgr"].party_of(-12) is None


if __name__ == "__main__":
    asyncio.run(test_create_and_move())
    print("OK: MAX creation, shared movement and character view")
