"""Shared Telegram/MAX auction application behavior with transactional fake DB."""
import ast
import asyncio
import runpy
import uuid
from contextlib import AsyncExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from bot.max_transport import MaxInput
from engine import content, econ_tx, money, uigate
from engine.character import Character


async def run():
    build = runpy.run_path('tests/test_econ_tx.py')['build']
    key = 'малое_зелье'
    assert key in content.ITEMS
    room = next(r for r, data in content.WORLD.items() if data.get('bank') or data.get('auction'))
    seller = Character(uid=-10, name='Продавец', cls='warrior', race='human')
    buyer = Character(uid=20, name='Покупатель', cls='warrior', race='human')
    for ch in (seller, buyer):
        ch.level, ch.room, ch.gold = 12, room, 1000000
    seller.inventory = [key, key]
    conn, cf = build({ch.uid: dict(gold=ch.gold, inventory=list(ch.inventory)) for ch in (seller, buyer)})
    async def save(ch, force=False):
        conn.characters[ch.uid] = dict(gold=ch.gold, inventory=list(ch.inventory))
    locks = {}
    env = dict(Character=Character, CallbackQuery=object, MaxInput=MaxInput,
               AsyncExitStack=AsyncExitStack, uuid=uuid, _uigate=uigate,
               WORLD=content.WORLD, ITEMS=content.ITEMS, econ_tx=econ_tx,
               db=SimpleNamespace(pool=SimpleNamespace(acquire=cf)),
               _econ_lock=lambda uid: locks.setdefault(uid, asyncio.Lock()),
               save=save, chars={seller.uid: seller, buyer.uid: buyer},
               send=AsyncMock(), show_auction=AsyncMock(), money=money,
               _notify=SimpleNamespace(ENABLED=False, emit=Mock()),
               _presence=SimpleNamespace(active=lambda _: True),
               weekly=SimpleNamespace(on_sell_lot=Mock()),
               _elog=SimpleNamespace(log_err=Mock()), _log=None)
    names = {'_auc_open', '_auc_price', 'auction_guard', '_auction_apply_data',
             'auction_action_core', '_max_auction_command', '_do_auc_buy_db'}
    tree = ast.parse(Path('bot/main.py').read_text(encoding='utf-8'))
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    assert len(nodes) == len(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'auction', 'exec'), env)
    command, action = env['_max_auction_command'], env['auction_action_core']
    event = MaxInput('42', 'message:listing', '/alist')
    assert not await command(seller, 'look', ['look'], event)
    assert await command(seller, 'alist', ['alist'], event)
    assert key in env['send'].await_args.args[1]
    await command(seller, 'alist', ['alist', key], event)
    lid = next(iter(conn.lots))
    assert seller.inventory.count(key) == 1
    await command(seller, 'alist', ['alist', key], event)  # same event, second copy still present
    assert seller.inventory.count(key) == 1 and len(conn.lots) == 1
    await command(buyer, 'auction', ['auction'], event)
    assert '/abuy ' + lid in env['send'].await_args.args[1]
    await command(seller, 'auction', ['auction'], event)
    assert '/acancel ' + lid in env['send'].await_args.args[1]
    assert not (await action(seller, 'buy', lid, 'own'))[0]
    assert not (await action(buyer, 'cancel', lid, 'other'))[0]
    before = buyer.gold
    buyer.level = 11
    assert not (await action(buyer, 'buy', lid, 'gate'))[0]
    buyer.level = 12
    buyer.room = next(r for r, data in content.WORLD.items() if not data.get('bank') and not data.get('auction'))
    assert not (await action(buyer, 'buy', lid, 'remote'))[0]
    buyer.room = room
    buyer.target = 'enemy'
    assert not (await action(buyer, 'buy', lid, 'combat'))[0]
    buyer.target = None
    quest_key = next(k for k, item in content.ITEMS.items() if item.get('type') == 'quest')
    seller.inventory.append(quest_key)
    assert not (await action(seller, 'list', quest_key, 'quest'))[0]
    # Telegram buys a MAX seller's listing through the same application core.
    cb = SimpleNamespace(id='callback:unique', answer=AsyncMock())
    original_save = env['save']
    async def fail_weekly(ch, force=False):
        if ch.uid == seller.uid and conn.lots[lid]['status'] == 'sold':
            assert seller.gold == conn.characters[seller.uid]['gold']
            assert buyer.gold == conn.characters[buyer.uid]['gold']
            raise RuntimeError('weekly save failed after economic commit')
        await original_save(ch, force)
    env['save'] = fail_weekly
    await env['_do_auc_buy_db'](cb, buyer, lid)
    price = conn.lots[lid]['price']
    assert buyer.gold == before - price and buyer.inventory.count(key) == 1
    assert seller.gold == 1000000 + price * 95 // 100
    assert cb.answer.await_args.kwargs['show_alert'] is False
    assert any(call.args[0] == seller.uid and 'продан' in call.args[1] for call in env['send'].await_args_list)
    env['save'] = original_save
    await command(buyer, 'abuy', ['abuy', lid], event)
    assert buyer.inventory.count(key) == 1 and buyer.gold == before - price
    assert env['weekly'].on_sell_lot.call_count == 1
    ok, _, listed = await action(seller, 'list', key, 'second')
    assert ok
    assert (await action(seller, 'cancel', listed['id'], 'cancel'))[0]
    assert (await action(seller, 'cancel', listed['id'], 'cancel'))[0]
    assert seller.inventory.count(key) == 1
    # Failure does not change inventory or publish a listing.
    conn.fail_commit = True
    assert not (await action(seller, 'list', key, 'failed'))[0]
    assert seller.inventory.count(key) == 1
    conn.fail_commit = False
    await command(buyer, 'auction', ['auction', '9999999'], event)
    assert 'Формат' in env['send'].await_args.args[1]


if __name__ == '__main__':
    asyncio.run(run())
    print('OK: MAX auction, Telegram interoperability, gates, repeat events, weekly failure and cache safety')
