"""Purchase buttons, explicit confirmation routing and recovery fences."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

from engine import content, game_actions, max_navigation as nav
from engine.character import Character, START_ROOM
from engine.shop_purchase import guard, recover
from bot.max_transport import MaxInput
from bot import commands
from test_max_gameplay import load_handler


async def run():
    ch = Character(uid=-1, name='Покупатель', race='human', cls='warrior')
    ch.init_vitals()
    ch.room = 'market'
    ch.gold = 10000
    vendor = game_actions.vendors_here(ch)[0]
    key = game_actions.shop_stock_here(ch, vendor)[1][0]
    menu = nav.shop_keyboard(ch, content.WORLD, vendor)
    assert nav.validate(menu) == menu
    label = next(b['text'] for row in menu for b in row if nav.command(b['text']) == f'/buyoffer {key}')
    assert not nav.command(label).startswith('/buy ')
    token = 'a'*32
    confirmation = nav.purchase_keyboard(token)
    assert nav.validate(confirmation) == confirmation
    assert nav.command(confirmation[0][0]['text']) == '/buyconfirm '+token
    assert nav.command(confirmation[1][0]['text']) == '/buycancel '+token
    for text in ('✅ Купить [abc]', '✅ Купить ['+token+'] /buy sword', '❌ Отмена ['+token.upper()+']'):
        assert nav.command(text) == text
        try:
            nav.validate([[{'type': 'message', 'text': text}]])
        except ValueError:
            pass
        else:
            raise AssertionError('Malformed confirmation accepted')
    tree = ast.parse(Path('bot/main.py').read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == '_max_shop_command')
    store = SimpleNamespace(quote=AsyncMock(return_value=(True, 'Цена', token)),
                            confirm=AsyncMock(return_value=(True, 'Куплено')))
    send, reply = AsyncMock(), AsyncMock()
    from engine.lifecycle_errors import StaleCharacterWrite
    env = dict(Character=Character, ShopPurchaseStore=lambda _: store, db=object(),
        _econ_lock=lambda _: asyncio.Lock(), _in_combat=lambda _: False,
        ui=SimpleNamespace(current_vendor=lambda _: vendor), game_actions=game_actions,
        ITEMS=content.ITEMS, _max_navigation=nav, send=send, _max_reply=reply,
        StaleCharacterWrite=StaleCharacterWrite, _evict_stale=Mock(),
        _elog=SimpleNamespace(log_err=Mock()), _log=None)
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'shop handler', 'exec'), env)
    command = env['_max_shop_command']
    assert await command(ch, 'buyoffer', ['buyoffer', key])
    store.quote.assert_awaited_once_with(ch, vendor, key, operation='buy')
    store.confirm.assert_not_awaited()
    assert send.await_args.kwargs['max_keyboard'] == confirmation
    assert ch.gold == 10000 and key not in ch.inventory
    await command(ch, 'buy', ['buy', key])  # text cannot bypass confirmation either
    assert store.quote.await_count == 2 and store.confirm.await_count == 0
    await command(ch, 'buyconfirm', ['buyconfirm', token])
    store.confirm.assert_awaited_with(ch, token, cancel=False, operation='buy')
    await command(ch, 'buycancel', ['buycancel', token])
    store.confirm.assert_awaited_with(ch, token, cancel=True, operation='buy')
    env['_in_combat'] = lambda _: True
    count = store.confirm.await_count
    await command(ch, 'buyconfirm', ['buyconfirm', token])
    assert store.confirm.await_count == count
    env['_in_combat'] = lambda _: False
    store.confirm.side_effect = RuntimeError('database unavailable')
    await command(ch, 'buyconfirm', ['buyconfirm', token])
    assert 'то же подтверждение' in reply.await_args.args[1]
    assert not await command(ch, 'look', ['look'])
    # Real MAX input normalization must route a product/confirmation label to the
    # confirmation service, never to the former in-memory /buy implementation.
    store.confirm.side_effect = None
    env.update(asyncio=asyncio, MaxInput=MaxInput, cmds=commands,
        db=SimpleNamespace(pool=object(), reserve_max_player_id=AsyncMock(return_value=-1)),
        _max_input_locks={}, _presence=SimpleNamespace(touch=Mock()), chars={-1: ch},
        _mod=SimpleNamespace(is_banned=lambda _: False),
        _max_preferences_command=AsyncMock(return_value=False),
        _max_auction_command=AsyncMock(return_value=False),
        _max_guild_command=AsyncMock(return_value=False),
        ui=SimpleNamespace(current_vendor=lambda _: vendor, DIR_ICONS={}))
    handler = load_handler(env)
    count = store.quote.await_count
    await handler(MaxInput('42', 'product', label))
    assert store.quote.await_count == count+1
    await handler(MaxInput('42', 'confirm', confirmation[0][0]['text']))
    store.confirm.assert_awaited_with(ch, token, cancel=False, operation='buy')
    ch.target = 'mob'
    assert guard(ch)
    ch.target = None
    # An uncertain COMMIT is reconciled once before a save can overwrite SQL.
    con = SimpleNamespace(fetchrow=AsyncMock(return_value={'status': 'done'}), transaction=asyncio.Lock)
    @asynccontextmanager
    async def acquire():
        yield con
    db = SimpleNamespace(_shop_uncertain={-1: (token, ch.generation, -200, key, 1)},
                         pool=SimpleNamespace(acquire=acquire))
    await recover(db, ch)
    assert ch.gold == 9800 and ch.inventory.count(key) == 1
    await recover(db, ch)
    assert ch.gold == 9800 and ch.inventory.count(key) == 1
    db._shop_uncertain[-1] = (token, ch.generation, -200, key, 1)
    con.fetchrow.side_effect = ConnectionError()
    try:
        await recover(db, ch)
    except ConnectionError:
        pass
    else:
        raise AssertionError('Uncertain outcome ignored')
    assert -1 in db._shop_uncertain


if __name__ == '__main__':
    asyncio.run(run())
    print('OK: MAX shop selection/confirmation/cancel, no text bypass, combat and uncertain-COMMIT save fence')
