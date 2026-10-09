"""Real item/combat routes, clean HTML, bounded history and every room UI."""
import ast
import asyncio
import copy
import html
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bot import ui, commands, mudnames
from bot.max_transport import MaxInput, MaxClient, parse_update
from engine import content, quest, max_ui, max_text, max_items, max_encounters, max_navigation, max_media
from engine.character import Character
from engine.world import World
from test_max_gameplay import load_handler
from test_max_transport import _Response


def plain(text):
    return html.unescape(re.sub(r'<[^>]*>', '', text))


def test_text():
    sample = '*Заголовок*\n**Жирный** и _подсказка_\n`/item руда_тумана`'
    result = max_text.to_html(sample)
    assert '<b>Заголовок</b>' in result and '<b>Жирный</b>' in result and '<i>подсказка</i>' in result
    assert '\\' not in result and '*' not in result and 'руда_тумана' in result
    attack = max_text.to_html('<script>alert(1)</script> [x](javascript:alert)')
    assert '<script>' not in attack and 'href=' not in attack
    assert 'script' in plain(attack)
    assert '<a href="https://' in max_text.to_html('[Документы](https://example.org/?a=1&b=2)')
    for value in ('<&'*8000, '*'+('а'*7001)+'*', '🗺 '*3000):
        parts = max_text.parts(value)
        assert ''.join(parts) == value
        assert all(len(max_text.to_html(part)) <= 3500 for part in parts)


async def run():
    max_ui._HISTORY.clear()
    ch = Character(uid=-501, name='Кнопки', race='human', cls='warrior')
    ch.init_vitals(); ch.init_skills(); ch.level = 40
    for menu in (max_navigation.purchase_keyboard('a'*32),
                 max_navigation.service_keyboard('b'*32, 'repair'),
                 max_navigation.context_keyboard(ch, content.WORLD),
                 max_navigation.errand_keyboard('c'*32)):
        clean = max_navigation.readable(max_ui.with_back(menu))
        assert max_navigation.validate(clean) == clean
        assert not any(re.search(r' \[[^\]]+\]$', b['text']) for row in clean for b in row)
        assert any(b['text'] == '⬅️ Назад' for row in clean for b in row)
    world = World()
    ch.room = 'cellar'
    rare = 'железный_меч#purple#42'
    ch.inventory = [rare, 'железный_меч', 'малое_зелье', 'малое_зелье']
    assert mudnames.match_item('железный_меч', ch.inventory) == 'железный_меч'
    assert mudnames.match_item(rare, ch.inventory) == rare
    sent, saved = AsyncMock(), AsyncMock()
    env = dict(asyncio=asyncio, MaxInput=MaxInput, Character=Character, content=content,
        db=SimpleNamespace(pool=object(), reserve_max_player_id=AsyncMock(return_value=ch.uid)),
        _max_input_locks={}, _presence=SimpleNamespace(touch=Mock()),
        _mod=SimpleNamespace(is_banned=lambda _: False), chars={ch.uid: ch},
        send=sent, cmds=commands, WORLD=content.WORLD, ITEMS=content.ITEMS,
        world=world, ui=ui, max_encounters=max_encounters, quest=quest,
        _max_preferences_command=AsyncMock(return_value=False),
        _max_guild_command=AsyncMock(return_value=False), _max_auction_command=AsyncMock(return_value=False),
        _in_combat=lambda actor: bool(actor.target), _combat_action_ready=AsyncMock(return_value=True),
        send_tutorial=AsyncMock(), save=saved, _evict_stale=Mock(),
        others_in=lambda _: [], analytics=SimpleNamespace(track_once=Mock()),
        combat=SimpleNamespace(player_basic_attack=Mock(return_value=['Удар'])),
        _max_combat_progress=AsyncMock(), gl=SimpleNamespace(on_mob_death=AsyncMock()))
    handler = load_handler(env)
    tree = ast.parse(Path('bot/main.py').read_text(encoding='utf-8'))
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name in ('text_action', '_consume_item')]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'shared actions', 'exec'), env)
    async def input(text):
        await handler(MaxInput('42', 'test:'+text, text))
    async def action(kind, key):
        await input(max_ui.payload(ch, kind, key))
    await input('/inv')
    await input('/item '+rare)
    assert ('equip', rare) in {max_ui.resolve(ch, b['payload']) for row in sent.await_args.kwargs['max_keyboard']
                             for b in row if b.get('payload', '').startswith('/ui ')}
    await action('equip', rare)
    assert ch.equipment['weapon'] == rare and rare in ch.inventory
    saved.assert_awaited_with(ch, force=True)
    await input('⬅️ Назад')
    assert 'Сумка' in plain(max_text.to_html(sent.await_args.args[1]))
    await input('/item '+rare)
    await action('unequip', rare)
    assert ch.equipment['weapon'] is None and ch.inventory.count(rare) == 1
    ch.hp = 1
    await action('use', 'малое_зелье')
    assert ch.hp > 1 and ch.inventory.count('малое_зелье') == 1
    saved.assert_awaited_with(ch, force=True)
    await action('use', 'малое_зелье')
    assert 'малое_зелье' not in ch.inventory
    before = copy.deepcopy(ch)
    await action('use', 'малое_зелье')
    assert ch == before  # A depleted/stale button never consumes another item.
    await input('/item '+rare)
    await input('⬅️ Назад')
    assert 'Сумка' in plain(max_text.to_html(sent.await_args.args[1]))
    await input('⬅️ Назад')
    assert 'Выходы' not in sent.await_args.args[1]
    mob = world.living_in(ch.room)[0]
    await action('mob', mob.key)
    assert sent.await_args.kwargs['max_image'] == 'mob:'+mob.mob_id
    before = copy.deepcopy(ch)
    old_hp = mob.hp
    await action('consider', mob.key)
    assert 'Оценка боя' in sent.await_args.args[1] and 'не гарантируется' in sent.await_args.args[1]
    assert ch == before and mob.hp == old_hp
    await action('attack', mob.key)
    assert ch.target == mob.key and ch.uid in mob.aggro
    env['combat'].player_basic_attack.assert_called_once_with(ch, mob)
    attack = max_ui.payload(ch, 'attack', mob.key)
    ch.room = 'village'
    await input(attack)
    assert env['combat'].player_basic_attack.call_count == 1  # No remote or retargeted hit.
    ch.target = None
    resident = content.WORLD[ch.room]['npc'][0]
    await action('npc', resident)
    assert resident in content.NPCS and 'Персонаж' not in sent.await_args.args[1][:10]
    await action('npc', 'жрец_храма')
    assert 'не находится' in sent.await_args.args[1]
    # Action tokens are bound to the actor, hero generation, room and expiry.
    token = max_ui.payload(ch, 'use', rare, now=1000)
    assert max_ui.resolve(ch, token, now=1001) == ('use', rare)
    assert max_ui.resolve(ch, token, now=999) is None
    assert max_ui.resolve(ch, token, now=1181) is None
    other = copy.deepcopy(ch); other.uid -= 1
    assert max_ui.resolve(other, token, now=1001) is None
    other = copy.deepcopy(ch); other.generation += 1
    assert max_ui.resolve(other, token, now=1001) is None
    assert max_ui.resolve(ch, token[:-1]+('0' if token[-1] != '0' else '1'), now=1001) is None
    # Failure evicts the mutated cache; no silent/repeated use/equip is allowed.
    saved.side_effect = ConnectionError()
    try:
        await env['_max_item_action'](ch, 'equip', rare)
    except ConnectionError:
        pass
    else:
        raise AssertionError('Persistence failure ignored')
    env['_evict_stale'].assert_called_with(ch.uid)
    saved.side_effect = None
    for room in content.WORLD:
        ch.room = room; ch.target = None
        text = max_encounters.room_card(ch, world, [])
        assert 'Выходы:' not in text and 'Куда направишься' not in text
        rows = max_ui.with_back(max_encounters.entity_rows(ch, world)+max_navigation.keyboard(ch, content.WORLD))
        assert max_navigation.validate(rows) == rows
        assert any(b['text'] == '⬅️ Назад' for row in rows for b in row)
        for key in content.WORLD[room].get('npc', []):
            assert any(max_ui.resolve(ch, b.get('payload')) == ('npc', key) for row in rows for b in row)
    ch.room = 'cellar'; mob = world.living_in(ch.room)[0]; ch.target = mob.key
    assert not any(max_navigation.command(b['text']) in content.WORLD[ch.room]['exits']
                   for row in max_navigation.keyboard(ch, content.WORLD) for b in row)
    mob.dead_at = 1
    calls = env['combat'].player_basic_attack.call_count
    await action('attack', mob.key)
    assert env['combat'].player_basic_attack.call_count == calls
    mob.dead_at = None; mob.hp = 0
    await action('attack', mob.key)
    assert env['combat'].player_basic_attack.call_count == calls
    assert max_media.asset_path('mob:'+mob.mob_id).name.endswith('.jpg')
    # Supported HTML reaches the actual HTTP request, not just a text helper.
    client = MaxClient('test-token')
    post = Mock(return_value=_Response()); client._session = SimpleNamespace(post=post)
    await client.send_chunk('42', '*Заголовок*\n_текст_')
    assert post.call_args.kwargs['json']['format'] == 'html'
    assert post.call_args.kwargs['json']['text'] == '<b>Заголовок</b>\n<i>текст</i>'
    update = {'update_type': 'message_callback', 'callback': {'user': {'user_id': 42},
              'callback_id': 'ui', 'payload': max_ui.payload(ch, 'mob', mob.key)},
              'message': {'recipient': {'chat_type': 'dialog'}}}
    assert parse_update(update) is not None
    update['callback']['payload'] = '/buyconfirm '+'a'*32
    assert parse_update(update).text == '/buyconfirm '+'a'*32
    update['callback']['payload'] = '/admin'
    assert parse_update(update) is None


if __name__ == '__main__':
    test_text()
    asyncio.run(run())
    print('OK: real MAX use/equip/unequip, exact seeded item, mob attack/compare, all 97 rooms/NPCs, back, strict HTML and stale/owner/failure guards')
