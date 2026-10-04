"""MAX combat presentation and shared skill routing, without transport effects."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from engine import max_combat, max_navigation
from engine.character import Character
from bot.max_transport import MaxInput


async def run():
    ch = Character(uid=-1, name='Боец', cls='warrior', race='human')
    ch.init_vitals()
    ch.init_skills()
    ch.hp = ch.max_hp
    mob = SimpleNamespace(meta={'name': '*Крыса*'}, hp=40, max_hp=100, aggro=[ch.uid])
    snapshot = max_combat.status(ch, mob)
    assert f'{ch.hp}/{ch.max_hp}' in snapshot and 'Крыса: 40/100' in snapshot
    assert not max_combat.low_health(ch)
    ch.hp = ch.max_hp // 4
    assert max_combat.low_health(ch) and '/flee' in max_combat.status(ch, mob)
    old = (ch.hp, ch.gold, ch.xp, ch.generation, mob.hp)
    max_combat.status(ch, mob)
    assert old == (ch.hp, ch.gold, ch.xp, ch.generation, mob.hp)
    ch.hp = 0
    assert 'Вы: 0/' in max_combat.status(ch) and max_combat.low_health(ch)
    assert max_combat.render('удар\nответ', 'здоровье').count('удар') == 1

    tree = ast.parse(Path('bot/main.py').read_text(encoding='utf-8'))
    names = {'_max_combat_progress', 'combat_hit', 'text_action'}
    nodes = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name in names]
    queue = AsyncMock(return_value=True)
    env = dict(Character=Character, _max_combat=max_combat,
               db=SimpleNamespace(pool=object(), max_external_user_id=AsyncMock(return_value='42')),
               MaxOutboxStore=lambda _: SimpleNamespace(enqueue_combat=queue),
               _elog=SimpleNamespace(log_err=Mock()), _log=None)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'combat', 'exec'), env)
    original_progress = env['_max_combat_progress']
    assert await env['_max_combat_progress'](ch, mob, ['удар'])
    assert queue.await_args.kwargs['urgent'] is True
    assert queue.await_args.args[5] == ch.generation
    queue.side_effect = RuntimeError('DB unavailable')
    assert not await env['_max_combat_progress'](ch, mob, ['удар'])
    env['_elog'].log_err.assert_called_once()
    # A UI failure never prevents the game loop from returning to its death path.
    await env['combat_hit'](ch, mob, ['смертельный удар'])
    queue.side_effect = None
    ch.hp = ch.max_hp
    order = []
    async def queue_progress(actor, target, lines, urgent=False):
        order.append(('progress', urgent, lines))
    async def reward(target, killers):
        order.append(('reward',))
    env.update(_max_combat_progress=queue_progress,
               _combat_action_ready=AsyncMock(return_value=True),
               _combat_mob=lambda _: mob, _combat_party=lambda _: [ch],
               world=SimpleNamespace(living_in=lambda _: [mob]), chars={ch.uid: ch},
               save=AsyncMock(), send_tutorial=AsyncMock(),
               analytics=SimpleNamespace(track_once=Mock()),
               _action_pacer=SimpleNamespace(refund=Mock()),
               gl=SimpleNamespace(on_mob_death=reward),
               combat=SimpleNamespace(use_skill=Mock(return_value=(True, ['магический удар']))))
    mob.hp = 0
    message = SimpleNamespace(answer=AsyncMock())
    with patch('bot.mudnames.match_skill', return_value='kick'):
        await env['text_action'](message, ch, 'cast', 'kick')
    assert order[0] == ('progress', True, ['магический удар']) and order[1] == ('reward',)
    message.answer.assert_not_awaited()  # no duplicate raw skill log after reward
    order.clear()
    env['combat'].use_skill.return_value = (False, ['Недостаточно ресурса'])
    with patch('bot.mudnames.match_skill', return_value='kick'):
        await env['text_action'](message, ch, 'cast', 'kick')
    assert not order
    message.answer.assert_awaited_once_with('Недостаточно ресурса', parse_mode='Markdown')
    # Telegram retains its normal successful skill response.
    ch.uid = 1
    mob.hp = 40
    env['combat'].use_skill.return_value = (True, ['Telegram удар'])
    with patch('bot.mudnames.match_skill', return_value='kick'):
        await env['text_action'](message, ch, 'cast', 'kick')
    assert message.answer.await_args.args == ('Telegram удар',)
    assert not order
    ch.uid = -1
    env['_combat_mob'] = lambda _: None
    env['_in_combat'] = lambda _: False
    env['combat'].use_skill.return_value = (True, ['Лечение вне боя'])
    with patch('bot.mudnames.match_skill', return_value='heal'):
        await env['text_action'](message, ch, 'cast', 'heal')
    assert not order and message.answer.await_args.args == ('Лечение вне боя',)
    env['_combat_mob'] = lambda _: mob
    # Real MAX basic-attack handler queues the final strike before its reward.
    handler = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == '_max_handle_input')
    ch.uid = -1
    mob.key, mob.mob_id, mob.hp = 'rat:1', 'rat', 10
    mob.aggro = [-1]
    env['db'].reserve_max_player_id = AsyncMock(return_value=-1)
    env.update(asyncio=asyncio, MaxInput=MaxInput, _max_input_locks={},
               _presence=SimpleNamespace(touch=Mock()), _mod=SimpleNamespace(is_banned=lambda _: False),
               cmds=SimpleNamespace(canonical=lambda value: value.lstrip('/')),
               _max_preferences_command=AsyncMock(return_value=False),
               _max_auction_command=AsyncMock(return_value=False),
               _max_guild_command=AsyncMock(return_value=False),
               ui=SimpleNamespace(DIR_ICONS={}), send=AsyncMock())
    def attack(actor, target):
        target.hp -= 10
        return ['последний удар']
    env['combat'].player_basic_attack = Mock(side_effect=attack)
    env.update(_max_reply=env['send'], _max_navigation=max_navigation)
    env['_max_shop_command'] = AsyncMock(return_value=False)
    env['_max_service_command'] = AsyncMock(return_value=False)
    env['_max_choice_command'] = AsyncMock(return_value=False)
    exec(compile(ast.Module(body=[handler], type_ignores=[]), 'MAX handler', 'exec'), env)
    await env['_max_handle_input'](MaxInput('42', 'attack:1', '/attack rat'))
    assert order == [('progress', True, ['последний удар']), ('reward',)]
    assert env['combat'].player_basic_attack.call_count == 1 and mob.hp == 0
    # Database presentation failure on a nonlethal hit still reaches game save.
    env['_max_combat_progress'] = original_progress
    queue.side_effect = RuntimeError('DB unavailable')
    env['save'].reset_mock()
    order.clear()
    mob.hp = 40
    await env['_max_handle_input'](MaxInput('42', 'attack:2', '/attack rat'))
    assert mob.hp == 30 and not order
    env['save'].assert_awaited_once_with(ch)


if __name__ == '__main__':
    asyncio.run(run())
    print('OK: MAX combat health snapshot, urgent warning, UI failure isolation, skill/reward order and Telegram regression')
