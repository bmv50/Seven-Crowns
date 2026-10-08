"""MAX map: directed graph, quest stages, private callbacks and durable snapshots."""
import asyncio
import base64
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image

from bot import commands
from bot.max_transport import MaxInput, parse_update
from engine import content, max_map, max_media, max_navigation, quest, errands
from engine.character import Character
from test_max_gameplay import load_handler


def hero(uid=-10):
    ch = Character(uid=uid, name='Путник', cls='warrior', race='human')
    ch.init_vitals()
    return ch


def test_plan():
    ch = hero()
    before = copy.deepcopy(ch)
    assert max_map.plan(ch) == (None, [], '')
    assert ch == before
    ch.quests['wolf_cull'] = 'active'
    before = copy.deepcopy(ch)
    qid, route, hint = max_map.plan(ch)
    assert qid == 'wolf_cull' and route == ['village', 'cellar']
    assert ch == before and '6' in max_map.caption(ch, qid, route, hint)
    ch.room = 'cellar'
    assert max_map.plan(ch)[1] == ['cellar']
    ch.quests['wolf_cull:kills'] = '6'
    assert max_map.plan(ch)[1] == ['cellar', 'village']
    assert 'выполнено' in max_map.plan(ch)[2]
    ch.quests['wolf_cull'] = 'done'
    assert max_map.plan(ch)[0] is None
    ch.flags['errand'] = {'npc': 'наставник', 'type': 'kill', 'mob': 'подвальная_крыса',
                          'count': 5, 'progress': 0}
    assert max_map.plan(ch)[0] == 'errand' and max_map.plan(ch)[1] == ['cellar']
    ch.flags['errand']['progress'] = 5
    assert max_map.plan(ch)[1] == ['cellar', 'village']
    ch.flags['map_quest'] = 'none'
    assert max_map.plan(ch)[0] is None
    # Planning reads real directed links, never invents a reverse exit.
    graph = {'a': {'exits': {'вниз': 'b'}}, 'b': {'exits': {'восток': 'c'}}, 'c': {'exits': {}}}
    assert max_map.shortest('a', ['c'], graph) == ['a', 'b', 'c']
    assert max_map.shortest('c', ['a'], graph) == []
    assert max_map.shortest('a', ['missing'], graph) == []
    for room in content.WORLD:
        for target in content.WORLD:
            route = max_map.shortest(room, [target])
            if route:
                assert route[0] == room and route[-1] == target
                assert len(set(route)) == len(route)
                assert all(b in content.WORLD[a]['exits'].values() for a, b in zip(route, route[1:]))
    # Every static quest type uses its actual current progress, including remort.
    ch = hero()
    ch.flags['remort'] = 3
    for qid in content.QUESTS:
        ch.quests = {qid: 'active'}
        before = copy.deepcopy(ch)
        _, route, hint = max_map.plan(ch)
        text = max_map.caption(ch, qid, route, hint)
        assert len(text) <= 3500 and content.QUESTS[qid]['name'] in text
        assert ch == before
        max_navigation.validate(max_map.keyboard(ch, route))
    ch.quests = {'remort_pack': 'active'}
    assert '0/4' in max_map.caption(ch, *max_map.plan(ch))


def test_callbacks():
    ch = hero()
    payload = max_map.walk_payload(ch, 'cellar', now=1000)
    assert max_map.resolve_walk(ch, payload, now=1001) == 'вниз'
    assert max_map.resolve_walk(hero(-11), payload, now=1001) is None
    assert max_map.resolve_walk(ch, payload, now=999) is None
    assert max_map.resolve_walk(ch, payload, now=5000) is None
    assert max_map.resolve_walk(ch, payload[:-1]+('0' if payload[-1] != '0' else '1'), now=1001) is None
    ch.room = 'market'
    assert max_map.resolve_walk(ch, payload, now=1001) is None
    ch.room = 'village'
    ch.flags['map_revision'] = 2
    assert max_map.resolve_walk(ch, payload, now=1001) is None
    payload = max_map.walk_payload(ch, 'cellar', now=1000)
    ch.quests['wolf_cull'] = 'active'
    assert max_map.resolve_walk(ch, payload, now=1001) is None
    payload = max_map.walk_payload(ch, 'cellar', now=1000)
    ch.flags['dead'] = True
    assert max_map.resolve_walk(ch, payload, now=1001) is None
    for invalid in (None, '/mapwalk', '/mapwalk village ../../secret 1000 '+'0'*32,
                    '/mapquest missing', '/maplist -1', '/maplist 9999999'):
        assert not max_map.valid_callback(invalid)
    update = {'update_type': 'message_callback', 'callback': {
        'user': {'user_id': 42}, 'callback_id': 'map1', 'payload': payload},
        'message': {'recipient': {'chat_type': 'dialog'}}}
    assert parse_update(update).text == payload
    update['message']['recipient']['chat_type'] = 'group'
    assert parse_update(update) is None
    assert len(max_map.quest_menu(ch, 999)) <= 13
    ch.quests = {qid: 'active' for qid in content.QUESTS}
    for page in range(10):
        max_navigation.validate(max_map.quest_menu(ch, page))


def test_snapshots():
    key = max_map.image_key('village', ['village', 'cellar'])
    assert max_map.snapshot(key) == ('village', ['village', 'cellar'])
    other = max_map.image_key('market', [])
    assert max_media.asset_path(key) != max_media.asset_path(other)
    for data in [['village', ['village', 'village']], ['village', ['cellar']],
                 ['village', ['village', 'abyss_eye']], ['../../.env', []], ['village', {}]]:
        invalid = 'map:'+base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip('=')
        try:
            max_map.snapshot(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError('Invalid map snapshot accepted')
    for invalid in ('map:../../.env', 'map:@@@@', 'map:'+'a'*9000):
        try:
            max_map.snapshot(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(invalid)
    with tempfile.TemporaryDirectory() as tmp, patch.object(max_map, 'ROOT', Path(tmp)):
        # Enqueue validation does not render or touch a file.
        assert not max_media.asset_path(key).exists()
        path = max_map.render_asset(key)
        with Image.open(path) as im:
            assert im.format == 'JPEG' and im.size == (1080, 980)
            im.verify()
        path.unlink()
        assert max_map.render_asset(key).is_file()  # Restart / cache loss is safe.
        assert max_map.render_asset(other).is_file()


async def test_handler():
    ch = hero(-10)
    sent, moves, map_reply = AsyncMock(), AsyncMock(return_value=(True, False)), AsyncMock()
    async def move(actor, direction):
        await moves(actor, direction)
        actor.room = content.WORLD[actor.room]['exits'][direction]
        actor.flags['map_revision'] = int(actor.flags.get('map_revision', 0))+1
        return True, False
    env = dict(asyncio=asyncio, MaxInput=MaxInput, Character=Character,
               db=SimpleNamespace(pool=object(), reserve_max_player_id=AsyncMock(return_value=-10)),
               _max_input_locks={}, _presence=SimpleNamespace(touch=Mock()),
               _mod=SimpleNamespace(is_banned=lambda _: False), chars={-10: ch},
               send=sent, _max_map_reply=map_reply, cmds=commands,
               ui=SimpleNamespace(DIR_ICONS=max_map.DIR_LABELS,
                                  render_room=lambda actor, *_: 'CARD:'+actor.room),
               WORLD=content.WORLD, RACES=content.RACES, world=object(), others_in=lambda _: [],
               _max_preferences_command=AsyncMock(return_value=False),
               _max_guild_command=AsyncMock(return_value=False),
               _max_auction_command=AsyncMock(return_value=False), move_core=move,
               quest=quest, weekly=SimpleNamespace(on_room_visit=Mock(return_value=None)),
               save=AsyncMock(), send_tutorial=AsyncMock(), analytics=SimpleNamespace(track_once=Mock()))
    handler = load_handler(env)
    payload = max_map.walk_payload(ch, 'cellar')
    await handler(MaxInput('42', 'mapwalk:1', payload))
    assert ch.room == 'cellar' and moves.await_count == 1
    map_reply.assert_awaited_with(ch)
    await handler(MaxInput('42', 'mapwalk:old', payload))
    assert ch.room == 'cellar' and moves.await_count == 1
    # A valid map callback still cannot bypass the shared combat gate.
    blocked = AsyncMock(return_value=(False, False))
    env['move_core'] = blocked
    payload = max_map.walk_payload(ch, 'village')
    await handler(MaxInput('42', 'mapwalk:combat', payload))
    blocked.assert_awaited_once_with(ch, 'вверх')
    assert ch.room == 'cellar' and 'боя' in sent.await_args.args[1]


if __name__ == '__main__':
    test_plan()
    test_callbacks()
    test_snapshots()
    asyncio.run(test_handler())
    print('OK: MAX map routing, quest/errand stages, signed owner-bound callbacks, stale/replayed controls, durable snapshots and shared movement')
