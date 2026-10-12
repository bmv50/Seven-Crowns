"""Real MAX corpse controls, shared consumption and stale/failure guards."""
import ast
import asyncio
import copy
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bot import commands, ui
from bot.max_transport import MaxInput, parse_update
from engine import content, max_encounters, max_navigation, max_ui
from engine.character import Character
from engine.lifecycle_errors import StaleCharacterWrite
from engine.loop import GameLoop
from engine.world import CORPSE_TTL, World
from test_max_gameplay import load_handler


def actor(uid=-501):
    ch = Character(uid=uid, name='Искатель'+str(abs(uid)), race='human', cls='warrior', room='cellar')
    ch.init_vitals(); ch.init_skills()
    return ch


def setup(ch, world, db=None, send=None):
    sent = send if send is not None else AsyncMock()
    database = db if db is not None else SimpleNamespace(
        pool=object(), reserve_max_player_id=AsyncMock(return_value=ch.uid), save=AsyncMock())
    env = dict(asyncio=asyncio, MaxInput=MaxInput, Character=Character, content=content,
        db=database, _max_input_locks={}, _presence=SimpleNamespace(touch=Mock()),
        _mod=SimpleNamespace(is_banned=lambda _: False), chars={ch.uid: ch},
        send=sent, cmds=commands, WORLD=content.WORLD, ITEMS=content.ITEMS,
        world=world, ui=ui, max_encounters=max_encounters,
        _max_preferences_command=AsyncMock(return_value=False),
        _max_guild_command=AsyncMock(return_value=False), _max_auction_command=AsyncMock(return_value=False),
        others_in=lambda _: [], _evict_stale=Mock())
    handler = load_handler(env)
    tree = ast.parse(Path('bot/main.py').read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'combat_reward')
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'MAX reward', 'exec'), env)
    return handler, env, sent


def loot_buttons(ch, rows):
    return [b for row in rows for b in row
            if (max_ui.resolve(ch, b.get('payload', '')) or ('',))[0] == 'loot']


def token(ch, corpse):
    return max_ui.payload(ch, 'loot', max_encounters.corpse_key(corpse))


def test_presentation():
    world = World(); ch = actor(); mob = world.living_in(ch.room)[0]
    corpses = [world.add_corpse(ch.room, mob, ['малое_зелье']) for _ in range(9)]
    world.add_corpse(ch.room, mob, [])
    before = copy.deepcopy(world.corpses)
    rows = max_ui.with_back(max_encounters.entity_rows(ch, world)+max_navigation.keyboard(ch, content.WORLD))
    assert max_navigation.validate(rows) == rows
    buttons = loot_buttons(ch, rows)
    assert len(buttons) == 6 and len({b['payload'] for b in buttons}) == 6
    assert all(b['text'] == '🎒 Обыскать: '+mob.meta['name'] for b in buttons)
    assert world.corpses == before and not ch.inventory
    corpses[0]['dead_at'] = time.time()-CORPSE_TTL-1
    assert corpses[0] not in max_encounters.lootable_corpses(ch, world)
    assert len(loot_buttons(ch, max_encounters.entity_rows(ch, world))) == 6
    for dead in (True, False):
        ch.flags['dead'] = dead; ch.hp = ch.max_hp if dead else 0
        assert not loot_buttons(ch, max_encounters.entity_rows(ch, world))
    ch = actor()
    ch.level = 60
    for room in content.WORLD:
        ch.room = room
        for _ in range(6):
            world.add_corpse(room, mob, ['малое_зелье'])
        menu = max_ui.with_back(max_encounters.entity_rows(ch, world)+max_navigation.keyboard(ch, content.WORLD))
        assert max_navigation.validate(menu) == menu
    ch.room = 'cellar'
    for key in ('corpse:1', 'corpse:1|', 'corpse:1|-1', 'corpse:1|1|2', 'remote|123'):
        try:
            max_ui.payload(ch, 'loot', key)
        except ValueError:
            pass
        else:
            raise AssertionError('Malformed corpse accepted: '+key)
    update = {'update_type': 'message_callback', 'callback': {'user': {'user_id': 42},
              'callback_id': 'loot', 'payload': token(ch, corpses[1])},
              'message': {'recipient': {'chat_type': 'dialog'}}}
    assert parse_update(update).text == update['callback']['payload']


async def run():
    world = World(); ch = actor(); mob = world.living_in(ch.room)[0]
    handler, env, sent = setup(ch, world)
    loop = GameLoop(world, {ch.uid: ch}, sent, AsyncMock())
    loop.on_combat_reward = env['combat_reward']
    ch.target = mob.key; mob.hp = 0; mob.aggro = [ch.uid]; mob.add_contrib(ch.uid, 1)
    with patch('engine.loop.random.random', return_value=0):
        await loop.on_mob_death(mob, [ch])
    assert ch.target is None and not mob.alive
    buttons = loot_buttons(ch, sent.await_args.kwargs['max_keyboard'])
    assert buttons and 'повержен' in sent.await_args.args[1]
    corpse = world.corpses_in(ch.room)[0]
    expected = list(corpse['loot']); payload = buttons[0]['payload']
    async def click(value=None):
        await handler(MaxInput('42', 'loot:test', payload if value is None else value))
    async def saved_before_success(saved):
        assert saved is ch and not world.find_corpse(ch.room, corpse['key'])
        assert 'Вы обыскали' not in sent.await_args.args[1]
    env['db'].save.side_effect = saved_before_success
    await click()
    assert ch.inventory == expected and 'Вы обыскали' in sent.await_args.args[1]
    assert not loot_buttons(ch, sent.await_args.kwargs['max_keyboard'])
    env['db'].save.assert_awaited_once_with(ch)
    await click()
    assert ch.inventory == expected and env['db'].save.await_count == 1
    assert 'уже обыскали' in sent.await_args.args[1]
    # Exact rare/seeded keys are retained; names never select another corpse.
    rare = 'железный_меч#purple#42'
    other = world.add_corpse(ch.room, mob, [rare, 'малое_зелье', 'малое_зелье'])
    payload = token(ch, other)
    env['db'].save.side_effect = None
    before = list(ch.inventory)
    await click('/loot '+max_encounters.corpse_key(other))
    assert ch.inventory == before and other['loot']
    for change in ('room', 'uid', 'generation', 'expiry'):
        saved_room, saved_uid, saved_generation = ch.room, ch.uid, ch.generation
        if change == 'room': ch.room = 'village'
        elif change == 'uid': ch.uid = -900
        elif change == 'generation': ch.generation += 1
        else:
            stale = max_ui.payload(ch, 'loot', max_encounters.corpse_key(other), now=time.time()-181)
        await click(stale if change == 'expiry' else payload)
        ch.room, ch.uid, ch.generation = saved_room, saved_uid, saved_generation
        assert ch.inventory == before and other['loot']
    # Decay is checked even when the periodic world tick hasn't removed the body.
    other['dead_at'] = time.time()-CORPSE_TTL-1
    await click(token(ch, other))
    assert ch.inventory == before and other['loot']
    # Reusing the sequence key after a restart cannot make an old control loot a new body.
    other['dead_at'] = time.time()-0.01
    await click(payload)
    assert ch.inventory == before and other['loot']
    other['dead_at'] = time.time()-1
    payload = token(ch, other)
    ch.flags['dead'] = True
    await click()
    assert ch.inventory == before and other['loot']
    ch.flags.clear(); ch.hp = 0
    await click()
    assert ch.inventory == before and other['loot']
    ch.hp = ch.max_hp
    await click()
    assert ch.inventory == before+[rare, 'малое_зелье', 'малое_зелье']
    # A shared corpse is consumed before save yields: two players cannot both take it.
    second = actor(-502); env['chars'][second.uid] = second
    env['db'].reserve_max_player_id.side_effect = lambda ext: ch.uid if ext == '42' else second.uid
    shared = world.add_corpse(ch.room, mob, [rare])
    entered, release = asyncio.Event(), asyncio.Event()
    async def pause_save(saved):
        entered.set(); await release.wait()
    env['db'].save.side_effect = pause_save
    first = asyncio.create_task(handler(MaxInput('42', 'loot:first', token(ch, shared))))
    await entered.wait()
    await handler(MaxInput('43', 'loot:second', token(second, shared)))
    release.set(); await first
    assert ch.inventory.count(rare) == 2 and second.inventory == []
    # Persistence failure must not announce success or restore possibly committed loot.
    for failure in (ConnectionError(), StaleCharacterWrite('stale')):
        failed = world.add_corpse(ch.room, mob, ['малое_зелье'])
        env['db'].save.side_effect = failure
        sent.reset_mock()
        try:
            await handler(MaxInput('42', 'loot:failure', token(ch, failed)))
        except type(failure):
            pass
        else:
            raise AssertionError('Save failure ignored')
        env['_evict_stale'].assert_called_with(ch.uid)
        sent.assert_not_awaited()
        assert world.find_corpse(ch.room, failed['key']) is None


if __name__ == '__main__':
    test_presentation()
    asyncio.run(run())
    print('OK: MAX kill-to-loot flow, seeded inventory, same-name corpses, concurrency, expiry, owner/room/generation and save failures')
