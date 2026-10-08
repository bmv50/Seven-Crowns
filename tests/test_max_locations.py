"""Full location coverage, room-card paths, no gameplay effects or arbitrary files."""
import ast
import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image

from bot import commands
from bot.max_transport import MaxInput
from engine import content, max_media, max_navigation, max_onboarding
from engine.character import Character
from test_max_gameplay import load_handler


def test_assets():
    assert max_media.PREVIEW_ROOMS == set(content.WORLD)
    manifests = [json.loads(Path('docs/'+name).read_text(encoding='utf-8'))
                 for name in ('LOCATION_PREVIEW_PROMPTS.json', 'LOCATION_ART_REMAINING_PROMPTS.json')]
    assets = [asset for manifest in manifests for asset in manifest['assets']]
    assert len(assets) == len({a['room'] for a in assets}) == len(content.WORLD)
    assert {a['room'] for a in assets} == set(content.WORLD)
    for asset in assets:
        assert asset['canonical_description'] == ' '.join(content.WORLD[asset['room']]['desc'].split())
        assert Path(asset['file']).resolve() == max_media.asset_path('room:'+asset['room'])
        assert asset['prompt']
    for room in max_media.PREVIEW_ROOMS:
        key = f'room:{room}'
        path = max_media.asset_path(key)
        assert path.is_file() and path.stat().st_size < 1024*1024
        with Image.open(path) as image:
            assert image.format == 'JPEG' and max(image.size) <= 1280
            assert image.width > image.height
            image.verify()
        assert max_media.room_asset(room) == key
        assert max_media.room_asset(room, enabled=False) is None
    assert max_media.room_asset('unknown_room') is None
    with patch.object(Path, 'is_file', return_value=False):
        assert max_media.room_asset('village') is None
    assert max_media.asset_path('human-mage') == max_onboarding.asset_path('human-mage')
    for key in ('room:../.env', 'room:village/../../.env', 'room:unknown_room', 'room:',
                '/etc/passwd', 'https://example.org/room.jpg'):
        try:
            max_media.asset_path(key)
        except ValueError:
            pass
        else:
            raise AssertionError('Unsafe location image allowed')
    welcome, menu, _ = max_onboarding.screen(max_onboarding.fresh())
    assert welcome.endswith('ваши решения.')
    assert all(text not in welcome for text in ('Создайте героя', 'Герой MAX', 'Уведомления'))
    assert menu[0][0]['text'] == 'Создать героя'


async def run():
    ch = Character(uid=-1, name='Путник', cls='warrior', race='human')
    ch.init_vitals()
    ch.init_skills()
    sent = AsyncMock()
    text_only = AsyncMock()
    async def move(actor, direction):
        actor.room = content.WORLD[actor.room]['exits'][direction]
        return True, False
    env = dict(asyncio=asyncio, MaxInput=MaxInput, Character=Character,
               db=SimpleNamespace(pool=object(), reserve_max_player_id=AsyncMock(return_value=-1)),
               _max_input_locks={}, _presence=SimpleNamespace(touch=Mock()),
               _mod=SimpleNamespace(is_banned=lambda _: False), chars={-1: ch},
               send=sent, _max_reply=text_only, cmds=commands,
               ui=SimpleNamespace(DIR_ICONS={'север': '↑', 'юг': '↓', 'вниз': '⇩'},
                                  render_room=lambda actor, *_: 'CARD:'+actor.room),
               WORLD=content.WORLD, RACES=content.RACES, world=object(), others_in=lambda _: [],
               _max_preferences_command=AsyncMock(return_value=False),
               _max_guild_command=AsyncMock(return_value=False),
               _max_auction_command=AsyncMock(return_value=False), move_core=move,
               quest=SimpleNamespace(on_enter_room=Mock(return_value=[])),
               weekly=SimpleNamespace(on_room_visit=Mock(return_value=None)),
               save=AsyncMock(), send_tutorial=AsyncMock(),
               analytics=SimpleNamespace(track_once=Mock()))
    handler = load_handler(env)
    before = copy.deepcopy(ch)
    await env['_max_room_reply'](ch, 'Событие')
    assert sent.await_args.args == (-1, 'Событие\n\nCARD:village')
    assert sent.await_args.kwargs['max_image'] == 'room:village'
    assert sent.await_args.kwargs['max_keyboard'] == max_navigation.keyboard(ch, content.WORLD)
    assert ch == before
    env['save'].assert_not_awaited()
    env['quest'].on_enter_room.assert_not_called()
    env['weekly'].on_room_visit.assert_not_called()
    ch.flags['roompics'] = False
    sent.reset_mock()
    await env['_max_room_reply'](ch)
    sent.assert_not_awaited()
    text_only.assert_awaited_with(-1, 'CARD:village')
    ch.flags['roompics'] = True
    with patch.object(Path, 'is_file', return_value=False):
        await env['_max_room_reply'](ch)
        assert text_only.await_args.args == (-1, 'CARD:village')
    for command, expected in (('/start', 'village'), ('/look', 'village'),
                              ('север', 'market'), ('юг', 'village'), ('вниз', 'cellar')):
        await handler(MaxInput('42', command, command))
        assert sent.await_args.kwargs['max_image'] == 'room:'+expected
        assert sent.await_args.args[1].endswith('CARD:'+expected)
    ch.flags['dead'] = True
    ch.hp = 0
    await handler(MaxInput('42', 'respawn', '/respawn'))
    assert ch.room == 'temple' and ch.hp == ch.max_hp
    assert sent.await_args.kwargs['max_image'] == 'room:temple'
    assert 'Возрождение' in sent.await_args.args[1]
    # Returning from a won fight uses the same location card, without extra rewards.
    tree = ast.parse(Path('bot/main.py').read_text(encoding='utf-8'))
    reward = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'combat_reward')
    exec(compile(ast.Module(body=[reward], type_ignores=[]), 'reward presentation', 'exec'), env)
    await env['combat_reward'](ch, 'Победа')
    assert sent.await_args.args[1] == 'Победа\n\nCARD:temple'
    assert sent.await_args.kwargs['max_image'] == 'room:temple'
    ch.room = 'inn'
    await env['_max_room_reply'](ch)
    assert sent.await_args.kwargs['max_image'] == 'room:inn'


if __name__ == '__main__':
    test_assets()
    asyncio.run(run())
    print('OK: MAX all location art, canonical prompt coverage, clean welcome, room cards, preferences, fallback, travel, respawn and combat return')
