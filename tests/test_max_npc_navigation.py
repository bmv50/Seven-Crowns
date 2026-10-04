"""Context menus use stable content IDs and the existing shared quest guards."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bot import commands
from bot.max_transport import MaxInput
from engine import content, errands, game_actions, max_navigation as nav, npc, quest, reputation
from engine.character import Character, START_ROOM
from test_max_gameplay import load_handler
from test_shared_npc_quests import load_core


def actions(rows):
    return [nav.command(button['text']) for row in rows for button in row]


def button(action):
    return next(label for label, command in nav.COMMANDS.items() if command == action)


async def run():
    ch = Character(uid=-1, name='Контекст', race='human', cls='warrior')
    ch.init_vitals()
    ch.init_skills()
    ch.room = START_ROOM
    state = (ch.gold, ch.xp, dict(ch.quests), dict(ch.flags))
    menu = nav.context_keyboard(ch, content.WORLD)
    here = content.WORLD[ch.room]['npc']
    assert {f'/talk {key}' for key in here} <= set(actions(menu))
    assert state == (ch.gold, ch.xp, ch.quests, ch.flags)
    for label in nav.COMMANDS:
        assert 1 <= len(label) <= 128 and '\n' not in label
    assert len(nav.COMMANDS) == len(set(nav.COMMANDS.values()))
    assert not any(action.startswith(('/buy ', '/choose ', '/confirm', '/learn ', '/repair'))
                   for action in nav.COMMANDS.values())
    for key in here:
        menu = nav.context_keyboard(ch, content.WORLD, key)
        assert nav.validate(menu) == menu
        assert set(actions(menu)) >= {f'/accept {q}' for q in quest.available_quests(ch, key)}
        if key in game_actions.vendors_here(ch):
            assert f'/shop {key}' in actions(menu) and '/sell' in actions(menu)
        if key == game_actions.trainer_here(ch):
            assert '/train' in actions(menu)
    assert not any(action.startswith(('/talk ', '/accept ', '/turnin ', '/shop '))
                   for action in actions(nav.context_keyboard(ch, content.WORLD, 'жрец_храма')))
    with patch.object(nav.quest, 'available_quests', return_value=list(content.QUESTS)):
        bounded = nav.context_keyboard(ch, content.WORLD, here[0])
        assert len(bounded) <= 14 and len(actions(bounded)) <= 16
        nav.validate(bounded)
    # IDs remain stable despite same/truncated display names.
    assert len({button(f'/talk {key}') for key in content.NPCS}) == len(content.NPCS)
    ch.flags['dead'] = True
    assert actions(nav.context_keyboard(ch, content.WORLD, here[0])) == actions(nav.keyboard(ch, content.WORLD))
    ch.flags['dead'] = False

    sent = AsyncMock()
    save = AsyncMock()
    talk = AsyncMock(return_value=('Диалог', None, ['Прогресс']))
    ui = SimpleNamespace(DIR_ICONS={}, active_vendor={}, current_vendor=lambda _: None)
    env = dict(asyncio=asyncio, Character=Character, MaxInput=MaxInput,
        db=SimpleNamespace(pool=object(), reserve_max_player_id=AsyncMock(return_value=-1)),
        _max_input_locks={}, _presence=SimpleNamespace(touch=Mock()),
        _mod=SimpleNamespace(is_banned=lambda _: False, is_muted=lambda _: False, chat_allowed=lambda _: True),
        chars={-1: ch}, send=sent, cmds=commands, ui=ui, WORLD=content.WORLD,
        _max_navigation=nav, QUESTS=content.QUESTS, ITEMS=content.ITEMS,
        _max_preferences_command=AsyncMock(return_value=False),
        _max_guild_command=AsyncMock(return_value=False), _max_auction_command=AsyncMock(return_value=False),
        quest=quest, npclib=npc, game_actions=game_actions, errands=errands,
        talk_core=talk, save=save, world=object(),
        analytics=SimpleNamespace(track_once=Mock()),
        gl=SimpleNamespace(_check_levelup=AsyncMock()), achievements=SimpleNamespace(check=lambda _: []),
        content=content, reputation=reputation, tutorial=SimpleNamespace(on_event=lambda *_: []))
    load_core(env)
    env['talk_core'] = talk
    handler = load_handler(env)
    tree = ast.parse(Path('bot/main.py').read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == '_max_context_reply')
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'context reply', 'exec'), env)
    async def click(action):
        await handler(MaxInput('42', 'event:' + action, button(action)))
    await click('/npcs')
    assert set(actions(sent.await_args.kwargs['max_keyboard'])) >= {f'/talk {key}' for key in here}
    giver = content.QUESTS['sample_reach_well']['giver']
    await click(f'/talk {giver}')
    talk.assert_awaited_once_with(ch, giver)
    # Progress precedes the dialog, so the latest message keeps context actions.
    assert 'Диалог' in sent.await_args.args[1]
    assert '/accept sample_reach_well' in actions(sent.await_args.kwargs['max_keyboard'])
    await click('/accept sample_reach_well')
    assert ch.quests['sample_reach_well'] == 'active'
    assert '/accept sample_reach_well' not in actions(sent.await_args.kwargs['max_keyboard'])
    saves = save.await_count
    await click('/accept sample_reach_well')
    assert save.await_count == saves
    ch.quests['sample_reach_well:reach'] = '1'
    assert '/turnin sample_reach_well' in actions(nav.context_keyboard(ch, content.WORLD, giver))
    before = ch.gold
    await click('/turnin sample_reach_well')
    assert ch.quests['sample_reach_well'] == 'done' and ch.gold == before + 1500
    await click('/turnin sample_reach_well')
    assert ch.gold == before + 1500
    # Stale NPC/shop buttons and remote quest offers cannot bypass proximity.
    ch.room = 'temple'
    talk.reset_mock()
    await click(f'/talk {giver}')
    talk.assert_not_awaited()
    await click('/accept посвящение_новичка')
    assert 'посвящение_новичка' not in ch.quests
    old_gold = ch.gold
    await click('/shop лавочник_туманного_брода')
    assert ui.active_vendor == {} and ch.gold == old_gold
    ch.flags['dead'] = True
    await click(f'/talk {giver}')
    talk.assert_not_awaited()
    assert 'пали' in sent.await_args.args[1]


if __name__ == '__main__':
    asyncio.run(run())
    print('OK: MAX NPC/quest menus, stable IDs, bounded labels, shared reward-once and stale-button guards')
