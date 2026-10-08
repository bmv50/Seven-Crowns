"""Real MAX handler item previews remain read-only and require current ownership."""
import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bot import commands
from bot.max_transport import MaxInput, parse_update, MaxClient
from engine import content, max_items, max_navigation, item_art
from engine.character import Character
from test_max_gameplay import load_handler
from test_max_transport import _Response


async def run():
    ch = Character(uid=-10, name='Путник', race='human', cls='warrior')
    ch.init_vitals()
    key = 'железный_меч#purple#42'
    ch.inventory = ['малое_зелье', key]
    sent = AsyncMock()
    env = dict(asyncio=asyncio, MaxInput=MaxInput, Character=Character,
        db=SimpleNamespace(pool=object(), reserve_max_player_id=AsyncMock(return_value=-10)),
        _max_input_locks={}, _presence=SimpleNamespace(touch=Mock()),
        _mod=SimpleNamespace(is_banned=lambda _: False), chars={-10: ch},
        send=sent, cmds=commands, WORLD=content.WORLD,
        ui=SimpleNamespace(DIR_ICONS={}, render_inventory=lambda _: 'INVENTORY',
                           item_caption=lambda item, ctx, actor: 'ITEM:'+item),
        _max_preferences_command=AsyncMock(return_value=False),
        _max_guild_command=AsyncMock(return_value=False),
        _max_auction_command=AsyncMock(return_value=False), save=AsyncMock())
    handler = load_handler(env)
    before = copy.deepcopy(ch)
    await handler(MaxInput('42', 'inv', '/inv'))
    menu = sent.await_args.kwargs['max_keyboard']
    assert max_navigation.validate(menu) == menu
    assert all('#42' not in b['text'] for row in menu for b in row)
    assert any(b.get('payload') == '/item '+key for row in menu for b in row)
    await handler(MaxInput('42', 'item', '/item '+key))
    assert sent.await_args.kwargs['max_image'] == item_art.image_key(key)
    assert sent.await_args.args[1] == 'ITEM:'+key and ch == before
    env['save'].assert_not_awaited()
    ch.inventory.remove(key)
    await handler(MaxInput('42', 'stale', '/item '+key))
    assert 'больше' in sent.await_args.args[1] and not sent.await_args.kwargs
    await handler(MaxInput('42', 'bad', '/item ../.env'))
    assert not sent.await_args.kwargs
    for bad in (None, '/item ../.env', '/invlist -1', '/invlist 9999999', '/item unknown'):
        assert not max_items.valid_callback(bad)
    ch.inventory = list(item_art.BASES)[:35]
    for page in (0, 1, 3, 999):
        assert len(max_items.inventory_keyboard(ch, page)) <= 12
        max_navigation.validate(max_items.inventory_keyboard(ch, page))
    update = {'update_type': 'message_callback', 'callback': {
        'user': {'user_id': 42}, 'callback_id': 'item1', 'payload': '/item малое_зелье'},
        'message': {'recipient': {'chat_type': 'dialog'}}}
    assert parse_update(update).text == '/item малое_зелье'
    # A rendering failure keeps the text and buttons deliverable, without
    # replaying a use/equip/buy operation or exposing arbitrary file paths.
    http = MaxClient('test-token')
    post = Mock(return_value=_Response())
    http._session = SimpleNamespace(post=post)
    fallback_menu = [[{'type': 'message', 'text': '🎒 Сумка'}]]
    with patch('engine.item_art.render_asset', side_effect=OSError('render unavailable')):
        await http.send_chunk('42', 'ITEM TEXT', image_asset=item_art.image_key('малое_зелье'), keyboard=fallback_menu)
    body = post.call_args.kwargs['json']
    assert body['text'] == 'ITEM TEXT'
    assert [a['type'] for a in body['attachments']] == ['inline_keyboard']


if __name__ == '__main__':
    asyncio.run(run())
    print('OK: MAX paginated inventory, safe exact seeded item previews, clean labels and current ownership; no game mutation')
