"""MAX service menus, actual handler routing and uncertain result fencing."""
import ast
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from bot import commands
from bot.max_transport import MaxInput
from engine import content, max_navigation as nav, skills
from engine.character import Character
from engine.lifecycle_errors import StaleCharacterWrite
from engine.service_purchase import recover
from test_max_gameplay import load_handler


async def run():
    ch = Character(uid=-1, name='Мастер', race='human', cls='warrior')
    ch.init_vitals()
    ch.init_skills()
    ch.room, ch.level, ch.gold = 'trainers_hall', 30, 100000
    token = 'd'*32
    for operation in ('repair', 'learn'):
        menu = nav.service_keyboard(token, operation)
        assert nav.validate(menu) == menu
        assert nav.command(menu[0][0]['text']) == f'/{operation}confirm {token}'
        assert nav.command(menu[1][0]['text']) == f'/{operation}cancel {token}'
    menu = nav.train_keyboard(ch, content.WORLD)
    assert nav.validate(menu) == menu
    assert '/learnoffer whirlwind' in [nav.command(b['text']) for r in menu for b in r]
    ch.room = 'mine_entrance'
    assert '/repair' in [nav.command(b['text']) for r in nav.context_keyboard(ch, content.WORLD, 'кузнец') for b in r]
    for text in ('✅ Починить [abc]', '✅ Изучить ['+token+'] /repair', '❌ Отмена обучения ['+token.upper()+']'):
        assert nav.command(text) == text
        try:
            nav.validate([[{'type': 'message', 'text': text}]])
        except ValueError:
            pass
        else:
            raise AssertionError('Malformed service button accepted')
    store = SimpleNamespace(quote=AsyncMock(return_value=(True, 'Цена', token)),
                            confirm=AsyncMock(return_value=(True, 'Готово')))
    env = dict(Character=Character, ServicePurchaseStore=lambda _: store,
        db=SimpleNamespace(pool=object(), reserve_max_player_id=AsyncMock(return_value=-1)),
        _econ_lock=lambda _: asyncio.Lock(), _in_combat=lambda _: False,
        send=AsyncMock(), _max_reply=AsyncMock(), _max_navigation=nav,
        StaleCharacterWrite=StaleCharacterWrite, _evict_stale=Mock(),
        _elog=SimpleNamespace(log_err=Mock()), _log=None)
    tree = ast.parse(Path('bot/main.py').read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == '_max_service_command')
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'service handler', 'exec'), env)
    route = env['_max_service_command']
    for parts in (['repair'], ['repair', 'confirm']):
        assert await route(ch, 'repair', parts)
        store.quote.assert_awaited_with(ch, 'repair', '')
        store.confirm.assert_not_awaited()
    await route(ch, 'learn', ['learn', 'whirlwind'])
    store.quote.assert_awaited_with(ch, 'learn', 'whirlwind')
    assert env['send'].await_args.kwargs['max_keyboard'] == nav.service_keyboard(token, 'learn')
    for operation in ('repair', 'learn'):
        for cancel in (False, True):
            command = operation + ('cancel' if cancel else 'confirm')
            await route(ch, command, [command, token])
            store.confirm.assert_awaited_with(ch, token, operation, cancel=cancel)
    count = store.quote.await_count
    env['_in_combat'] = lambda _: True
    await route(ch, 'repair', ['repair'])
    assert store.quote.await_count == count
    env['_in_combat'] = lambda _: False
    store.confirm.side_effect = ConnectionError()
    await route(ch, 'learnconfirm', ['learnconfirm', token])
    assert 'то же подтверждение' in env['_max_reply'].await_args.args[1]
    store.confirm.side_effect = None
    env.update(asyncio=asyncio, MaxInput=MaxInput, cmds=commands, _max_input_locks={},
        _presence=SimpleNamespace(touch=Mock()), chars={-1: ch},
        _mod=SimpleNamespace(is_banned=lambda _: False),
        _max_preferences_command=AsyncMock(return_value=False),
        _max_auction_command=AsyncMock(return_value=False),
        _max_guild_command=AsyncMock(return_value=False), ui=SimpleNamespace(DIR_ICONS={}))
    handler = load_handler(env)
    await handler(MaxInput('42', 'confirm', nav.service_keyboard(token, 'learn')[0][0]['text']))
    store.confirm.assert_awaited_with(ch, token, 'learn', cancel=False)
    # New actions are blocked if reconciliation fails, before any game mutation.
    env['db']._service_uncertain = {-1: ('unknown',)}
    env['db'].save = AsyncMock(side_effect=ConnectionError())
    count = store.quote.await_count
    try:
        await handler(MaxInput('42', 'fenced', '/repair'))
    except ConnectionError:
        pass
    else:
        raise AssertionError('New action bypassed reconciliation')
    assert store.quote.await_count == count
    # Recovery applies deltas once, preserving reward, wear and unrelated flags.
    ch.equipment['weapon'] = 'ржавый_меч'
    ch.set_durab('weapon', 48)
    ch.flags['other'] = True
    con = SimpleNamespace(fetchrow=AsyncMock(return_value={'status': 'done'}), transaction=asyncio.Lock)
    @asynccontextmanager
    async def acquire():
        yield con
    db = SimpleNamespace(pool=SimpleNamespace(acquire=acquire), _service_uncertain={
        -1: (token, ch.generation, -100, {'durab': {'weapon': ['ржавый_меч', 50]}})})
    await recover(db, ch)
    assert ch.gold == 99900 and ch.durab('weapon') == 98 and ch.flags['other']
    await recover(db, ch)
    assert ch.gold == 99900 and ch.durab('weapon') == 98
    db._service_uncertain[-1] = (token, ch.generation, -100, {'durab': {'weapon': ['wrong', 50]}})
    try:
        await recover(db, ch)
    except RuntimeError:
        pass
    else:
        raise AssertionError('Equipment mismatch ignored')
    assert ch.gold == 99900 and -1 in db._service_uncertain


if __name__ == '__main__':
    asyncio.run(run())
    print('OK: MAX repair/learning confirmation menus, no text bypass, combat, actual input routing and recovery fence')
