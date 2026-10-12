"""Callback source routing, safe HTTP edits/fallback and rollback switch."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bot.max_transport import MaxClient, MaxInput, parse_update
from engine import max_panel, max_ui, max_navigation, max_combat, content
from engine.character import Character
from engine.max_outbox import MaxOutboxWorker, MaxSendError
from test_max_outbox import Response, row


def update(mid='mid.source'):
    return {'update_type': 'message_callback',
            'callback': {'user': {'user_id': 42}, 'callback_id': 'cb', 'payload': '/inv'},
            'message': {'sender': {'is_bot': True}, 'body': {'mid': mid},
                        'recipient': {'chat_type': 'dialog', 'user_id': 42}}}


def test_source():
    assert parse_update(update()).source_message_id == 'mid.source'
    data = update(); data['message']['sender']['is_bot'] = False
    assert parse_update(data).source_message_id is None
    for mid in ('', 'x'*257, '../other', 'a b', None):
        assert parse_update(update(mid)).source_message_id is None
    data = update(); data['message']['recipient']['user_id'] = 43
    assert parse_update(data) is None
    assert MaxInput('42', 'message', '/look').source_message_id is None
    with patch.dict('os.environ', {'MAX_EDIT_CARDS': '1'}):
        with max_panel.reply_scope('42', 'mid.source'):
            assert max_panel.target('42') == 'mid.source' and max_panel.target('43') is None
            with max_panel.reply_scope('43', 'mid.other'):
                assert max_panel.target('42') is None
            assert max_panel.target('42') == 'mid.source'
        assert max_panel.target('42') is None
    with patch.dict('os.environ', {'MAX_EDIT_CARDS': '0'}), max_panel.reply_scope('42', 'mid.source'):
        assert max_panel.target('42') is None


async def test_http():
    for status, payload, expected in ((200, {'success': True}, None),
                                      (200, {'success': False}, 502), (404, {}, 404),
                                      (429, {}, 429), (500, {}, 500)):
        client = MaxClient('TOKEN_MUST_NOT_LEAK')
        put = Mock(return_value=Response(status, payload))
        post = Mock()
        client._session = SimpleNamespace(put=put, post=post)
        try:
            mid = await client.send_chunk('42', '*Панель*', edit_message_id='mid.source')
        except MaxSendError as exc:
            assert exc.status == expected and 'TOKEN_MUST_NOT_LEAK' not in str(exc)
        else:
            assert expected is None and mid == 'mid.source'
        assert put.call_args.kwargs['params'] == {'message_id': 'mid.source'}
        assert put.call_args.kwargs['json'] == {
            'text': '<b>Панель</b>', 'format': 'html', 'notify': False, 'attachments': []}
        assert put.call_args.kwargs['allow_redirects'] is False
        post.assert_not_called()
    client = MaxClient('test')
    client._session = SimpleNamespace(put=Mock(return_value=Response(200, {'success': True})))
    client.image_payload = AsyncMock(return_value={'photos': {'1': {'token': 'uploaded'}}})
    await client.send_chunk('42', 'Новая локация', keyboard=[[{'type':'message','text':'🔍 Осмотр'}]],
                            image_asset='room:cellar', edit_message_id='mid.source')
    attachments = client._session.put.call_args.kwargs['json']['attachments']
    assert [a['type'] for a in attachments] == ['image', 'inline_keyboard']


async def test_worker():
    claimed = row(uid=-1, generation=1, panel_key='1:cellar', edit_message_id='mid.source')
    def fixture(errors):
        store = SimpleNamespace(claim=AsyncMock(return_value=dict(claimed)), finish=AsyncMock(),
                                combat_current=AsyncMock(return_value=True),
                                abandon_edit=AsyncMock(return_value=True),
                                active_panel=AsyncMock(return_value='mid.active'))
        client = SimpleNamespace(send_chunk=AsyncMock(side_effect=errors))
        return store, client
    for error in (MaxSendError(403), MaxSendError(404)):
        store, client = fixture([error, 'mid.new'])
        assert await MaxOutboxWorker(store, client).process_one()
        assert client.send_chunk.await_args_list[0].kwargs == {'edit_message_id':'mid.source'}
        assert client.send_chunk.await_args_list[1].kwargs == {}
        store.abandon_edit.assert_awaited_once()
        store.finish.assert_awaited_once_with(claimed, 'sent', delivered_message_id='mid.new')
    for error in (TimeoutError(), MaxSendError(429), MaxSendError(500), MaxSendError(400), MaxSendError(401)):
        store, client = fixture([error])
        await MaxOutboxWorker(store, client).process_one()
        assert client.send_chunk.await_count == 1
        store.abandon_edit.assert_not_awaited()
        assert store.finish.await_args.args[1] == ('pending' if isinstance(error, TimeoutError)
                                                  or error.status in (429,500) else 'failed')
    store, client = fixture([MaxSendError(404)])
    store.abandon_edit.return_value = False
    await MaxOutboxWorker(store, client).process_one()
    assert client.send_chunk.await_count == 1
    store.finish.assert_not_awaited()  # Lease recovery owns this row now.
    store, client = fixture(['mid.active'])
    store.claim.return_value.update(combat_key='1:cellar', edit_message_id=None)
    await MaxOutboxWorker(store, client).process_one()
    assert client.send_chunk.await_args.kwargs['edit_message_id'] == 'mid.active'
    store, client = fixture(['mid.new'])
    store.claim.return_value['edit_message_id'] = ''
    await MaxOutboxWorker(store, client).process_one()
    store.active_panel.assert_not_awaited()
    assert client.send_chunk.await_args.kwargs == {}
    store, client = fixture(['mid.new'])
    with patch.dict('os.environ', {'MAX_EDIT_CARDS':'0'}):
        await MaxOutboxWorker(store, client).process_one()
    assert client.send_chunk.await_args.kwargs == {}
    store, client = fixture([]); store.combat_current.return_value = False
    await MaxOutboxWorker(store, client).process_one()
    client.send_chunk.assert_not_awaited()


async def test_application():
    source = Path('bot/main.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    names = ('send', '_max_enqueue_input', '_max_inbox_worker', '_max_combat_progress')
    nodes = [n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name in names]
    queued = AsyncMock()
    batched = AsyncMock()
    ch = Character(uid=-1, name='Панель', cls='mage', race='human', room='cellar')
    other = Character(uid=-2, name='Другой', cls='mage', race='human', room='cellar')
    db = SimpleNamespace(pool=object(), max_external_user_id=AsyncMock(
        side_effect=lambda uid: '42' if uid == -1 else '43'), enqueue_max_update=AsyncMock(),
        claim_next_max_update=AsyncMock(side_effect=[{
            'external_user_id':'42', 'event_key':'callback:42:cb', 'message_text':'/inv',
            'source_message_id':'mid.source'}, asyncio.CancelledError()]),
        finish_max_update=AsyncMock())
    ns = dict(asyncio=asyncio, db=db, chars={-1:ch,-2:other}, MaxInput=MaxInput,
              max_ui=max_ui, _max_navigation=max_navigation,
              WORLD=content.WORLD, _max_combat=max_combat,
              MaxOutboxStore=lambda _: SimpleNamespace(enqueue=queued, enqueue_combat=batched),
              _max_client=SimpleNamespace(answer_callback=AsyncMock()))
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'panel application','exec'),ns)
    menu = max_ui.with_back([[{'type':'message','text':'🔍 Осмотр'}]])
    async def handler(event):
        assert event.source_message_id == 'mid.source'
        await ns['send'](-1, 'Сумка', max_keyboard=menu)
        assert queued.await_args.kwargs == {'keyboard':menu,'edit_message_id':'mid.source',
                                            'panel_key':'1:cellar','generation':ch.generation}
        await ns['send'](-2, 'Другой герой', max_keyboard=menu)
        assert 'edit_message_id' not in queued.await_args.kwargs
        await ns['send'](-1, 'Важное событие')
        assert not queued.await_args.kwargs
        await ns['_max_combat_progress'](ch,None,['Удар'])
        assert batched.await_args.kwargs['edit_message_id'] == 'mid.source'
    ns['_max_handle_input'] = handler
    await ns['_max_enqueue_input'](MaxInput('42','callback:42:cb','/inv','mid.source'))
    db.enqueue_max_update.assert_awaited_once_with('callback:42:cb','42','/inv',source_message_id='mid.source')
    try:
        await ns['_max_inbox_worker']()
    except asyncio.CancelledError:
        pass
    assert max_panel.target('42') is None
    db.finish_max_update.assert_awaited_once_with('callback:42:cb')
    await ns['_max_combat_progress'](ch,None,['Фоновый удар'])
    assert 'edit_message_id' not in batched.await_args.kwargs
    with max_panel.reply_scope('42',None):
        await ns['_max_combat_progress'](ch,None,['Текстовая команда'])
    assert batched.await_args.kwargs['edit_message_id'] == ''


if __name__ == '__main__':
    test_source()
    asyncio.run(test_http())
    asyncio.run(test_worker())
    asyncio.run(test_application())
    print('OK: MAX source-card edit routing, image replacement, durable fallback, stale hero, rollback switch and context isolation')
