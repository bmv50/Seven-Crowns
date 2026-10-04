"""Shared guild application guards and MAX command routing, without network."""
import ast
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from bot.max_transport import MaxInput
from engine import content, textsafe, uigate, guild as guildlib
from engine.character import Character
from engine.guild import GuildManager


async def run():
    source = Path('bot/main.py').read_text(encoding='utf-8')
    names = {'_guild_apply_snapshot', 'guild_action_core', 'guild_bank_core',
             'guild_chat_core', '_max_guild_command', 'render_guild'}
    nodes = [n for n in ast.parse(source).body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    assert len(nodes) == len(names)
    @asynccontextmanager
    async def lock(uid):
        yield
    mgr = GuildManager.__new__(GuildManager)
    mgr.guilds, mgr.member_of, mgr.invites, mgr._next, mgr.db_mode = {}, {}, {}, 1, True
    ch = Character(uid=-10, name='Лидер', cls='warrior', race='human')
    target = Character(uid=20, name='Напарник', cls='warrior', race='human')
    ch.level = target.level = 10
    mgr.create(ch.uid, 'Стражи')
    state = (mgr.guilds, mgr.member_of, mgr.invites, mgr._next)
    store = SimpleNamespace(action=AsyncMock(return_value=((True, 'OK'), state)),
                            load=AsyncMock(return_value=state))
    tx = SimpleNamespace(deposit_gold=AsyncMock(return_value=(True, 'Внесено', 90, 10)))
    env = dict(Character=Character, MaxInput=MaxInput, _uigate=uigate, _ts=textsafe,
               _guild_state_lock=asyncio.Lock(), _econ_lock=lock, guild_mgr=mgr,
               guild_store=store, guild_tx=tx, db=SimpleNamespace(pool=SimpleNamespace(acquire=Mock())),
               chars={ch.uid: ch, target.uid: target}, save=AsyncMock(), send=AsyncMock(),
               ITEMS=content.ITEMS, guildlib=guildlib, _time_mod=SimpleNamespace(time_ns=lambda: 1),
               _elog=SimpleNamespace(log_err=Mock()), _log=None,
               money=SimpleNamespace(fmt=str), players_in_room=lambda _: [target],
               analytics=SimpleNamespace(track=Mock()),
               _mod=SimpleNamespace(is_muted=lambda _: False, chat_allowed=lambda _: True))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'guilds', 'exec'), env)
    command = env['_max_guild_command']
    event = MaxInput('42', 'message:unique', '/guild')
    assert not await command(ch, 'look', ['look'], event)
    assert await command(ch, 'guild', ['guild'], event)
    assert '/gdeposit' in env['send'].await_args.args[1]
    await command(ch, 'ginvite', ['ginvite', '20'], event)
    assert store.action.await_args.kwargs['target'] == 20
    assert any(call.args[0] == 20 for call in env['send'].await_args_list)
    target.room = 'other_room'
    calls = store.action.await_count
    await command(ch, 'ginvite', ['ginvite', '20'], event)
    assert store.action.await_count == calls
    target.room = ch.room
    ch.level = 9
    await command(ch, 'gcreate', ['gcreate', 'Стражи'], event)
    assert store.action.await_count == calls
    ch.level = 10
    await command(ch, 'gdeposit', ['gdeposit', '-1'], event)
    assert not tx.deposit_gold.await_count
    await command(ch, 'gdeposit', ['gdeposit', '10'], event)
    assert tx.deposit_gold.await_args.args[-1] == 'max:guild:-10:message:unique'
    assert ch.gold == 90 and mgr.guilds['1']['bank_gold'] == 10
    # Failure cannot mutate the published cache or report success.
    store.action.side_effect = RuntimeError('database unavailable')
    before = mgr.member_of.copy()
    ok, _ = await env['guild_action_core'](ch, 'leave')
    assert not ok and mgr.member_of == before
    tx.deposit_gold.side_effect = RuntimeError('commit failed')
    ok, _ = await env['guild_bank_core'](ch, 'deposit_gold', 10, 'failure')
    assert not ok and ch.gold == 90
    mgr.guilds['1']['members'].append(20)
    await command(ch, 'gchat', ['gchat', 'Привет'], event)
    assert any(call.args[0] == 20 and 'Привет' in call.args[1]
               for call in env['send'].await_args_list)
    env['_mod'].is_muted = lambda _: True
    calls = env['send'].await_count
    await command(ch, 'gchat', ['gchat', 'Мут'], event)
    assert env['send'].await_count == calls + 1
    assert 'недоступен' in env['send'].await_args.args[1]


if __name__ == '__main__':
    asyncio.run(run())
    print('OK: MAX guild routing, shared gates, proximity, moderation, bank identity and failure safety')
