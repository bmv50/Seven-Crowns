"""Allowlisted MAX navigation, current-state guards and durable HTTP payloads."""
import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from bot.max_transport import MaxClient, MaxInput, parse_update
from engine import max_navigation as nav
from engine.character import Character
from engine.max_outbox import MaxOutboxWorker
from test_max_gameplay import load_handler
from test_max_transport import _Response, _message


def labels(rows):
    return {button['text'] for row in rows for button in row}


async def run():
    ch = Character(uid=-1, name='Навигатор', race='human', cls='warrior')
    ch.init_vitals()
    ch.room = 'a'
    rooms = {'a': {'exits': {'север': 'b'}}, 'b': {'exits': {'юг': 'a'}}}
    assert labels(nav.keyboard(None, rooms)) == {'❓ Помощь'}
    for level in (1, 3, 10, 12):
        ch.level = level
        menu = nav.keyboard(ch, rooms)
        assert nav.validate(menu) == menu
        shown = labels(menu)
        assert '↑ Север' in shown and '↓ Юг' not in shown
        assert ('👥 Группа' in shown) == (level >= 3)
        assert ('🏰 Гильдия' in shown) == (level >= 10)
        assert ('⚖️ Аукцион' in shown) == (level >= 12)
    ch.hp = 0
    assert labels(nav.keyboard(ch, rooms)) == {'✨ Возродиться', '⚙️ Настройки', '🔔 Уведомления', '❓ Помощь'}
    ch.hp = ch.max_hp
    ch.flags['dead'] = True
    assert '↑ Север' not in labels(nav.keyboard(ch, rooms))
    ch.flags['dead'] = False
    for label, command in nav.COMMANDS.items():
        assert nav.command(label) == command
    assert nav.command('👤 Герой /buy sword') == '👤 Герой /buy sword'
    assert nav.command('/look') == '/look'
    for invalid in (None, [], [[]], [[{'type': 'callback', 'text': '👤 Герой'}]],
                    [[{'type': 'message', 'text': []}]],
                    [[{'type': 'message', 'text': '/buy sword'}]],
                    [[{'type': 'message', 'text': '👤 Герой', 'payload': 'x'}]]):
        try:
            nav.validate(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError('Unsafe keyboard accepted')
    menu = nav.keyboard(ch, rooms)
    copy = nav.validate(menu)
    menu[0][0]['text'] = 'invalid'
    assert copy[0][0]['text'] == '↑ Север'

    # Current character state is consulted for every reply, not captured at start.
    tree = ast.parse(Path('bot/main.py').read_text(encoding='utf-8'))
    reply = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == '_max_reply')
    sent = AsyncMock()
    env = dict(send=sent, chars={-1: ch}, WORLD=rooms, _max_navigation=nav)
    exec(compile(ast.Module(body=[reply], type_ignores=[]), 'reply', 'exec'), env)
    await env['_max_reply'](-1, 'room')
    assert '↑ Север' in labels(sent.await_args.kwargs['max_keyboard'])
    ch.room = 'b'
    await env['_max_reply'](-1, 'room')
    assert '↓ Юг' in labels(sent.await_args.kwargs['max_keyboard'])
    # Old direction buttons use CURRENT exits; combat/death still gate movement.
    from bot import commands
    ch.room = 'a'
    mover = AsyncMock(return_value=(False, False))
    handler_env = dict(asyncio=asyncio, MaxInput=MaxInput, Character=Character,
        db=SimpleNamespace(pool=object(), reserve_max_player_id=AsyncMock(return_value=-1)),
        _max_input_locks={}, _presence=SimpleNamespace(touch=Mock()),
        _mod=SimpleNamespace(is_banned=lambda _: False), chars={-1: ch}, send=sent,
        cmds=commands, ui=SimpleNamespace(DIR_ICONS={'север': '↑', 'юг': '↓'}), WORLD=rooms,
        _max_preferences_command=AsyncMock(return_value=False),
        _max_guild_command=AsyncMock(return_value=False),
        _max_auction_command=AsyncMock(return_value=False), move_core=mover)
    handler = load_handler(handler_env)
    event = parse_update(_message(text='↓ Юг'))
    await handler(event)
    mover.assert_not_awaited()
    assert sent.await_args.args[1] == 'Туда нельзя пройти.'
    await handler(MaxInput('42', 'north', '↑ Север'))
    mover.assert_awaited_once_with(ch, 'север')
    assert 'боя' in sent.await_args.args[1] and ch.room == 'a'
    ch.flags['dead'] = True
    await handler(MaxInput('42', 'dead', '↑ Север'))
    assert mover.await_count == 1 and 'пали' in sent.await_args.args[1]
    ch.flags['dead'] = False

    # Persisted keyboard survives JSON decoding and retries without re-running input.
    menu = nav.keyboard(ch, rooms)
    row = dict(id=1, external_user_id='42', message_text='room', attempts=1,
               expired=False, lease_token='lease', keyboard=json.dumps(menu))
    store = SimpleNamespace(claim=AsyncMock(return_value=row), finish=AsyncMock())
    client = SimpleNamespace(send_chunk=AsyncMock(side_effect=TimeoutError()))
    worker = MaxOutboxWorker(store, client)
    await worker.process_one()
    assert store.finish.await_args.args[1] == 'pending'
    client.send_chunk.assert_awaited_once_with('42', 'room', keyboard=menu)
    client.send_chunk.side_effect = None
    await worker.process_one()
    assert client.send_chunk.await_args.kwargs['keyboard'] == menu
    assert store.finish.await_args.args[1] == 'sent'

    calls = []
    http = MaxClient('test-token')
    def post(url, **kwargs):
        calls.append(kwargs)
        return _Response()
    http._session = SimpleNamespace(post=post)
    await http.send_chunk('42', 'room', keyboard=menu)
    assert calls[0]['json'] == {'text': 'room', 'attachments': [
        {'type': 'inline_keyboard', 'payload': {'buttons': menu}}]}
    try:
        await http.send_chunk('42', 'room', keyboard=[[{'type': 'link', 'text': '👤 Герой'}]])
    except ValueError:
        pass
    else:
        raise AssertionError('Unsafe HTTP attachment accepted')
    assert len(calls) == 1


if __name__ == '__main__':
    asyncio.run(run())
    print('OK: MAX navigation labels, menus, current-room/combat/death guards, retry and HTTP keyboard')
