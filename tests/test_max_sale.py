"""Sale labels, explicit confirmation, equipment filters and input recovery fence."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from contextlib import asynccontextmanager

from bot import commands
from bot.max_transport import MaxInput
from engine import content, game_actions, max_navigation as nav, money
from engine.character import Character
from engine.lifecycle_errors import StaleCharacterWrite
from engine.shop_purchase import recover
from test_max_gameplay import load_handler


async def run():
    ch = Character(uid=-1, name='Продавец', race='human', cls='warrior')
    ch.init_vitals()
    ch.room = 'market'
    key, vendor = 'ржавый_меч', 'оружейник_брода'
    ch.inventory = [key]
    ch.equipment['weapon'] = key
    assert not any(nav.command(b['text']) == f'/selloffer {key}'
                   for row in nav.sell_keyboard(ch, content.WORLD, vendor) for b in row)
    ch.inventory.append(key)
    menu = nav.sell_keyboard(ch, content.WORLD, vendor)
    nav.validate(menu)
    label = next(b['text'] for row in menu for b in row if nav.command(b['text']) == f'/selloffer {key}')
    token = 'b'*32
    confirmation = nav.purchase_keyboard(token, 'sell')
    nav.validate(confirmation)
    assert nav.command(confirmation[0][0]['text']) == '/sellconfirm '+token
    assert nav.command(confirmation[1][0]['text']) == '/sellcancel '+token
    store = SimpleNamespace(quote=AsyncMock(return_value=(True, 'Вы получите', token)),
                            confirm=AsyncMock(return_value=(True, 'Продано')))
    send, reply = AsyncMock(), AsyncMock()
    env = dict(Character=Character, ShopPurchaseStore=lambda _: store,
        db=SimpleNamespace(pool=object(), reserve_max_player_id=AsyncMock(return_value=-1)),
        _econ_lock=lambda _: asyncio.Lock(), _in_combat=lambda _: False,
        ui=SimpleNamespace(current_vendor=lambda _: vendor, DIR_ICONS={}),
        game_actions=game_actions, ITEMS=content.ITEMS, WORLD=content.WORLD, money=money,
        _max_navigation=nav, send=send, _max_reply=reply,
        StaleCharacterWrite=StaleCharacterWrite, _evict_stale=Mock(),
        _elog=SimpleNamespace(log_err=Mock()), _log=None)
    tree = ast.parse(Path('bot/main.py').read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == '_max_shop_command')
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'sale command', 'exec'), env)
    command = env['_max_shop_command']
    await command(ch, 'sell', ['sell'])
    assert '/selloffer '+key in [nav.command(b['text']) for row in reply.await_args.kwargs['max_keyboard'] for b in row]
    store.quote.assert_not_awaited()
    await command(ch, 'selloffer', ['selloffer', key])
    store.quote.assert_awaited_once_with(ch, vendor, key, operation='sell')
    store.confirm.assert_not_awaited()
    assert send.await_args.kwargs['max_keyboard'] == confirmation
    await command(ch, 'sell', ['sell', key])
    assert store.quote.await_count == 2  # text also requires confirmation
    await command(ch, 'sellconfirm', ['sellconfirm', token])
    store.confirm.assert_awaited_with(ch, token, cancel=False, operation='sell')
    await command(ch, 'sellcancel', ['sellcancel', token])
    store.confirm.assert_awaited_with(ch, token, cancel=True, operation='sell')
    assert ch.inventory.count(key) == 2 and ch.equipment['weapon'] == key
    env.update(asyncio=asyncio, MaxInput=MaxInput, cmds=commands,
        _max_input_locks={}, _presence=SimpleNamespace(touch=Mock()), chars={-1: ch},
        _mod=SimpleNamespace(is_banned=lambda _: False),
        _max_preferences_command=AsyncMock(return_value=False),
        _max_auction_command=AsyncMock(return_value=False), _max_guild_command=AsyncMock(return_value=False))
    handler = load_handler(env)
    await handler(MaxInput('42', 'sell-offer', label))
    assert store.quote.await_count == 3
    await handler(MaxInput('42', 'sell-confirm', confirmation[0][0]['text']))
    store.confirm.assert_awaited_with(ch, token, cancel=False, operation='sell')
    # An uncertain receipt blocks NEW input before item use or another trade.
    env['db']._shop_uncertain = {-1: (token, ch.generation, 100, key, -1)}
    env['db'].save = AsyncMock(side_effect=ConnectionError())
    count = store.quote.await_count
    try:
        await handler(MaxInput('42', 'fenced-input', label))
    except ConnectionError:
        pass
    else:
        raise AssertionError('Uncertain trade allowed new input')
    assert store.quote.await_count == count
    con = SimpleNamespace(fetchrow=AsyncMock(return_value={'status': 'done'}), transaction=asyncio.Lock)
    @asynccontextmanager
    async def acquire(): yield con
    db = SimpleNamespace(pool=SimpleNamespace(acquire=acquire),
                         _shop_uncertain={-1: (token, ch.generation, 100, key, -1)})
    before = ch.gold
    await recover(db, ch)
    await recover(db, ch)
    assert ch.gold == before+100 and ch.inventory.count(key) == 1
    assert ch.equipment['weapon'] == key


if __name__ == '__main__':
    asyncio.run(run())
    print('OK: MAX sale selection/confirmation/cancel, equipped-copy filtering, text routing and recovery-before-input')
