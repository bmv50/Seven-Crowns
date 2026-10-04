"""Errand buttons bind an offer, keep current NPC guards and persist before display."""
import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from bot import commands
from bot.max_transport import MaxInput
from engine import content, errands, game_actions, max_navigation as nav, npc, money
from engine.character import Character, START_ROOM
from engine.lifecycle_errors import StaleCharacterWrite
from test_max_gameplay import load_handler


async def run():
    ch = Character(uid=-1, name='Поручение', race='human', cls='warrior')
    ch.init_vitals()
    ch.room = START_ROOM
    giver = 'наставник'
    menu = nav.context_keyboard(ch, content.WORLD, giver)
    assert nav.validate(menu) == menu
    label = next(b['text'] for r in menu for b in r if nav.command(b['text']) == '/errand '+giver)
    sent = AsyncMock()
    db = SimpleNamespace(pool=object(), save=AsyncMock(), reserve_max_player_id=AsyncMock(return_value=-1))
    env = dict(asyncio=asyncio, uuid=uuid, Character=Character, MaxInput=MaxInput,
        db=db, chars={-1: ch}, cmds=commands, send=sent,
        _max_input_locks={}, _presence=SimpleNamespace(touch=Mock()),
        _mod=SimpleNamespace(is_banned=lambda _: False),
        _max_preferences_command=AsyncMock(return_value=False),
        _max_guild_command=AsyncMock(return_value=False), _max_auction_command=AsyncMock(return_value=False),
        ui=SimpleNamespace(DIR_ICONS={}), WORLD=content.WORLD, npclib=npc,
        errands=errands, game_actions=game_actions, money=money, save=AsyncMock(),
        complete_errand_core=AsyncMock(return_value=(True, 'Сдано')),
        StaleCharacterWrite=StaleCharacterWrite, _evict_stale=Mock(),
        _elog=SimpleNamespace(log_err=Mock()), _log=None)
    handler = load_handler(env)
    await handler(MaxInput('42', 'offer', label))
    db.save.assert_awaited_once_with(ch)
    offer = ch.flags['errand_pending']
    token = offer['_max_token']
    acceptance = sent.await_args.kwargs['max_keyboard']
    assert nav.validate(acceptance) == acceptance
    assert nav.command(acceptance[0][0]['text']) == '/erracceptoffer '+token
    assert not errands.has_active(ch)
    # Redisplaying the same proposal does not randomize its target or token.
    await handler(MaxInput('42', 'same', label))
    assert ch.flags['errand_pending'] == offer
    await handler(MaxInput('42', 'foreign', '✅ Принять поручение ['+'f'*32+']'))
    assert not errands.has_active(ch) and errands.taken_today(ch) == 0
    # Old button cannot accept a newer proposal even from the same NPC.
    ch.flags['errand_pending'] = dict(offer, _max_token='f'*32)
    await handler(MaxInput('42', 'old', acceptance[0][0]['text']))
    assert not errands.has_active(ch)
    ch.flags['errand_pending'] = offer
    # Current proximity, not the displayed NPC, determines acceptance.
    ch.room = 'temple'
    await handler(MaxInput('42', 'remote', acceptance[0][0]['text']))
    assert not errands.has_active(ch)
    ch.room = START_ROOM
    await handler(MaxInput('42', 'accept', acceptance[0][0]['text']))
    assert errands.has_active(ch) and errands.taken_today(ch) == 1
    await handler(MaxInput('42', 'duplicate', acceptance[0][0]['text']))
    assert errands.taken_today(ch) == 1
    active = ch.flags['errand']
    if active['type'] == 'kill':
        active['progress'] = active['count']
    else:
        ch.inventory.extend([active['item']]*active['count'])
    menu = nav.context_keyboard(ch, content.WORLD, giver)
    assert '/errturnin '+giver in [nav.command(b['text']) for r in menu for b in r]
    await handler(MaxInput('42', 'wrong-giver', '/errturnin староста'))
    env['complete_errand_core'].assert_not_awaited()
    await handler(MaxInput('42', 'turnin', '/errturnin '+giver))
    env['complete_errand_core'].assert_awaited_once_with(ch, giver)
    # No acceptance keyboard is exposed if the durable offer save fails.
    ch.flags.pop('errand')
    ch.flags.pop('errand_day')
    db.save.side_effect = ConnectionError()
    sent.reset_mock()
    await handler(MaxInput('42', 'unavailable', label))
    assert 'Не удалось сохранить' in sent.await_args.args[1]
    assert 'max_keyboard' not in sent.await_args.kwargs
    db.save.side_effect = StaleCharacterWrite('stale')
    await handler(MaxInput('42', 'stale', label))
    env['_evict_stale'].assert_called_once_with(-1)
    # Tokens use the same strict message-button allowlist, never arbitrary code.
    malformed = '✅ Принять поручение ['+token+'] extra'
    assert nav.command(malformed) == malformed
    try:
        nav.validate([[{'type': 'message', 'text': malformed}]])
    except ValueError:
        pass
    else:
        raise AssertionError('Unsafe offer label accepted')


if __name__ == '__main__':
    asyncio.run(run())
    print('OK: MAX errand NPC/offer menus, persist-before-display, token binding, stale/remote/duplicate guards and failure handling')
