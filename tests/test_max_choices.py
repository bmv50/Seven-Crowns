"""Story buttons select first; durable confirmation is routed independently."""
import ast
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from bot import commands
from bot.max_transport import MaxInput
from engine import content, max_navigation as nav
from engine.character import Character
from engine.lifecycle_errors import StaleCharacterWrite
from engine.max_choice import recover
from test_max_gameplay import load_handler


async def run():
    ch = Character(uid=-1, name='Выбор', race='human', cls='warrior')
    ch.init_vitals()
    ch.room = 'temple'
    qid, option = 'sample_choose_faith', 'light'
    ch.quests[qid] = 'active'
    giver = content.QUESTS[qid]['giver']
    menu = nav.context_keyboard(ch, content.WORLD, giver)
    assert nav.validate(menu) == menu
    label = next(b['text'] for r in menu for b in r if nav.command(b['text']) == f'/choose {qid} {option}')
    token = 'e'*32
    menu = nav.choice_keyboard(token)
    assert nav.validate(menu) == menu
    assert nav.command(menu[0][0]['text']) == '/choiceconfirm '+token
    assert nav.command(menu[1][0]['text']) == '/choicecancel '+token
    for text in ('✅ Подтвердить путь [bad]', '❌ Отмена выбора ['+token+'] extra'):
        assert nav.command(text) == text
        try:
            nav.validate([[{'type': 'message', 'text': text}]])
        except ValueError:
            pass
        else:
            raise AssertionError('Malformed story confirmation accepted')
    store = SimpleNamespace(quote=AsyncMock(return_value=(True, 'Предложение', token)),
                            confirm=AsyncMock(return_value=(True, 'Выбрано')))
    env = dict(Character=Character, ChoiceStore=lambda _: store,
        db=SimpleNamespace(pool=object(), reserve_max_player_id=AsyncMock(return_value=-1)),
        _econ_lock=lambda _: asyncio.Lock(), _in_combat=lambda _: False,
        send=AsyncMock(), _max_reply=AsyncMock(), _max_navigation=nav,
        StaleCharacterWrite=StaleCharacterWrite, _evict_stale=Mock(),
        _elog=SimpleNamespace(log_err=Mock()), _log=None)
    tree = ast.parse(Path('bot/main.py').read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == '_max_choice_command')
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'choice handler', 'exec'), env)
    route = env['_max_choice_command']
    await route(ch, 'choose', ['choose', qid, option])
    store.quote.assert_awaited_once_with(ch, qid, option)
    store.confirm.assert_not_awaited()
    assert 'quest_choices' not in ch.flags
    assert env['send'].await_args.kwargs['max_keyboard'] == menu
    await route(ch, 'confirm', ['confirm'])
    store.confirm.assert_awaited_with(ch, '', cancel=False)
    await route(ch, 'choicecancel', ['choicecancel', token])
    store.confirm.assert_awaited_with(ch, token, cancel=True)
    env.update(asyncio=asyncio, MaxInput=MaxInput, cmds=commands, _max_input_locks={},
        _presence=SimpleNamespace(touch=Mock()), chars={-1: ch},
        _mod=SimpleNamespace(is_banned=lambda _: False),
        _max_preferences_command=AsyncMock(return_value=False),
        _max_auction_command=AsyncMock(return_value=False),
        _max_guild_command=AsyncMock(return_value=False), ui=SimpleNamespace(DIR_ICONS={}))
    handler = load_handler(env)
    await handler(MaxInput('42', 'choose', label))
    store.quote.assert_awaited_with(ch, qid, option)
    await handler(MaxInput('42', 'confirm', menu[0][0]['text']))
    store.confirm.assert_awaited_with(ch, token, cancel=False)
    env['db']._choice_uncertain = {-1: ('unknown',)}
    env['db'].save = AsyncMock(side_effect=ConnectionError())
    count = store.quote.await_count
    try:
        await handler(MaxInput('42', 'fenced', label))
    except ConnectionError:
        pass
    else:
        raise AssertionError('Input bypassed choice reconciliation')
    assert store.quote.await_count == count
    con = SimpleNamespace(fetchrow=AsyncMock(return_value={'status': 'done'}), transaction=asyncio.Lock)
    @asynccontextmanager
    async def acquire():
        yield con
    db = SimpleNamespace(pool=SimpleNamespace(acquire=acquire),
                         _choice_uncertain={-1: (token, ch.generation, qid, option)})
    ch.flags['unrelated'] = True
    await recover(db, ch)
    assert ch.flags['quest_choices'][qid] == option and ch.flags['unrelated']
    await recover(db, ch)
    assert not db._choice_uncertain


if __name__ == '__main__':
    asyncio.run(run())
    print('OK: MAX story selection/confirmation/cancel, bounded exact labels, real input routing and recovery fence')
