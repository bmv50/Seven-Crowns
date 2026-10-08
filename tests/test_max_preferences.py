"""MAX map and explicit settings, including failures and dead-hero controls."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from engine import content, notify, player_settings, max_map
from engine.character import Character
from engine.lifecycle_errors import StaleCharacterWrite


async def run():
    notify.ENABLED = True
    ch = Character(uid=-10, name='Тестер', cls='warrior', race='human')
    ch.init_vitals()
    ch.flags['quest_progress'] = 42
    messages = []
    async def send(uid, message, **kwargs):
        messages.append((uid, message))
    async def setting(actor, key, value):
        player_settings.apply(actor.flags, player_settings.patch_for(key, value))
    database = SimpleNamespace(pool=object(), set_player_setting=AsyncMock(side_effect=setting))
    env = dict(Character=Character, db=database, _notify=notify, _max_client=object(), WORLD=content.WORLD,
               send=send, _max_reply=send, StaleCharacterWrite=StaleCharacterWrite, _evict_stale=Mock(),
               _elog=SimpleNamespace(log_err=Mock()), _log=None,
               ui=SimpleNamespace(render_settings=lambda _: 'SETTINGS', render_notify=lambda _: 'NOTIFY'))
    env.update(max_map=max_map, save=AsyncMock())
    names = {'player_setting_core', '_max_preferences_command', '_max_map_reply', 'render_map'}
    tree = ast.parse(Path('bot/main.py').read_text(encoding='utf-8'))
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    assert len(nodes) == len(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'preferences', 'exec'), env)
    command, core = env['_max_preferences_command'], env['player_setting_core']
    assert not await command(ch, 'look', ['look'])
    assert await command(ch, 'map', ['map'])
    assert content.WORLD[ch.room]['name'] in messages[-1][1]
    for direction, dest in content.WORLD[ch.room]['exits'].items():
        assert direction.lower() in messages[-1][1].lower() and content.WORLD[dest]['name'] in messages[-1][1]
    old_room = ch.room
    ch.room = next(iter(content.WORLD[ch.room]['exits'].values()))
    await command(ch, 'map', ['map'])
    assert f"Вы здесь: {content.WORLD[ch.room]['name']}" in messages[-1][1]
    assert ch.room != old_room
    await command(ch, 'settings', ['settings'])
    assert '/settings autoloot' in messages[-1][1]
    await command(ch, 'settings', ['settings', 'autoloot', 'on'])
    await command(ch, 'settings', ['settings', 'autoloot', 'on'])
    assert ch.flags['autoloot'] is True and ch.flags['quest_progress'] == 42
    before = database.set_player_setting.await_count
    await command(ch, 'settings', ['settings', 'dead', 'off'])
    await command(ch, 'notify', ['notify', 'admin', 'on'])
    await command(ch, 'notify', ['notify', 'on'])
    assert database.set_player_setting.await_count == before + 1
    assert notify.opted_in(ch)
    await command(ch, 'notify', ['notify', 'on'])
    assert notify.opted_in(ch)  # explicit setting, not a toggle
    env['_max_client'] = None
    assert not (await core(ch, 'push', 'on'))[0]
    env['_max_client'] = object()
    assert (await core(ch, 'push', 'ON'))[0]
    await command(ch, 'notify', ['notify', 'quiet', 'on'])
    await command(ch, 'notify', ['notify', 'tz', '+12'])
    await command(ch, 'notify', ['notify', 'limit', '5'])
    await command(ch, 'notify', ['notify', 'world_boss', 'off'])
    assert not notify.quiet_off(ch) and notify.tz_offset(ch) == 12
    assert notify.limit(ch) == 5 and not notify.enabled(ch, 'world_boss')
    assert not (await core(ch, 'tz', '13'))[0]
    assert not (await core(ch, 'limit', '100'))[0]
    database.set_player_setting.side_effect = RuntimeError('commit failed')
    assert not (await core(ch, 'autoloot', 'off'))[0]
    assert ch.flags['autoloot'] is True
    database.set_player_setting.side_effect = setting
    ch.flags['dead'] = True
    await command(ch, 'settings', ['settings', 'autoloot', 'off'])
    await command(ch, 'notify', ['notify', 'off'])
    assert ch.flags['autoloot'] is False and not notify.opted_in(ch)
    await command(ch, 'map', ['map'])
    assert '/respawn' in messages[-1][1]
    assert ch.flags['dead'] is True
    database.set_player_setting.side_effect = StaleCharacterWrite('stale')
    assert not (await core(ch, 'autoloot', 'on'))[0]
    env['_evict_stale'].assert_called_once_with(ch.uid)
    # Every unsupported flag is refused before persistence.
    for key in ('gold', 'dead', 'pk', 'admin', 'notify_quota'):
        try:
            player_settings.patch_for(key, 'on')
        except ValueError:
            pass
        else:
            raise AssertionError(key)


if __name__ == '__main__':
    asyncio.run(run())
    print('OK: MAX current map, explicit preferences, allowlist, consent, death and failure handling')
