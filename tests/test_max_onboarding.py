"""All wizard paths, clean callbacks, identity guards and actual creation routing."""
import ast
import asyncio
import copy
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bot import commands
from bot.max_transport import MaxClient, parse_update, MaxInput, API_URL
from engine import max_onboarding as wizard, max_navigation as nav, content, textsafe, starter
from engine.character import Character, START_ROOM
from engine.lifecycle_errors import NameTaken, ActiveCharacterExists
from engine.max_outbox import MaxSendError, MaxOutboxWorker
from test_max_gameplay import load_handler
from test_max_transport import _Response


def click(state, action):
    return f"/onboard {state['token']} {action}"


def test_paths():
    assert len(wizard.asset_keys()) == 31
    for race in content.RACES:
        s = wizard.fresh()
        text, menu, image = wizard.screen(s)
        assert image == 'world' and menu[0][0]['text'] == 'Создать героя'
        old = copy.deepcopy(s)
        s = wizard.advance(s, click(s, 'begin'))
        assert s['step'] == 'races' and s['token'] != old['token']
        assert wizard.advance(s, click(old, 'begin')) is None
        s = wizard.advance(s, click(s, 'race '+race))
        assert s['step'] == 'race_preview' and wizard.screen(s)[2] == race
        card = wizard.race_card(race)
        assert 'Преимущества' in card and 'Недостатки' in card
        assert wizard.advance(s, click(s, 'cancel'))['step'] == 'races'
        s = wizard.advance(s, click(s, 'confirm'))
        assert s['step'] == 'classes'
        assert wizard.advance(s, click(s, 'cancel'))['step'] == 'race_preview'
        for cls in content.CLASSES:
            chosen = wizard.advance(s, click(s, 'class '+cls))
            if cls not in wizard.allowed_classes(race):
                assert chosen is None
                continue
            assert chosen['step'] == 'class_preview'
            text, menu, image = wizard.screen(chosen)
            assert image == f'{race}-{cls}' and content.RACES[race]['name'] in text
            assert 'Преимущества' in text and 'Недостатки' in text
            assert [r[0]['text'] for r in menu] == ['Подтвердить', 'Отмена']
            assert wizard.advance(chosen, click(chosen, 'cancel'))['step'] == 'classes'
            named = wizard.advance(chosen, click(chosen, 'confirm'))
            assert named['step'] == 'name'
            assert wizard.advance(named, click(named, 'confirm')) is None
            assert wizard.advance(named, click(named, 'cancel'))['step'] == 'class_preview'
            assert nav.validate(wizard.screen(named)[1]) == wizard.screen(named)[1]
        for state in (old, s):
            assert nav.validate(wizard.screen(state)[1]) == wizard.screen(state)[1]
    assert 'Получаемый опыт +15%' in wizard.race_card('human')
    assert 'Получаемый опыт -15%' in wizard.race_card('orc')
    with patch('engine.rules2.ENABLED', True):
        assert 'святой урон' in wizard.race_card('orc') and 'урон +50%' in wizard.race_card('orc')
        assert 'урон −33%' in wizard.race_card('dwarf')
    with patch('engine.rules2.ENABLED', False):
        assert 'Уязвимость' not in wizard.race_card('orc')
    for value in (None, '', '/onboard x begin', '/onboard '+'a'*32+' buy sword', '/create human mage Evil'):
        assert not wizard.valid_callback(value)
    for key in ('../.env', '/etc/passwd', 'https://host/image.jpg', 'orc-mage', None):
        try:
            wizard.asset_path(key)
        except ValueError:
            pass
        else:
            raise AssertionError('Arbitrary image reference allowed')
    # Every displayed image must be shipped as a valid, bounded JPEG in Docker.
    from PIL import Image
    for key in wizard.asset_keys():
        path = wizard.asset_path(key)
        assert path.is_file() and path.stat().st_size < 1024*1024, key
        with Image.open(path) as art:
            assert art.format == 'JPEG' and max(art.size) <= 1280 and min(art.size) >= 800
            art.verify()
    token = 'a'*32
    accept = nav.terms_keyboard(token)[0][0]
    assert accept == {'type': 'callback', 'text': '✅ Принимаю условия', 'payload': '/termsagree '+token}
    event = {'update_type': 'message_callback', 'callback': {
        'user': {'user_id': 42}, 'callback_id': 'cb:1', 'payload': accept['payload']},
        'message': {'recipient': {'chat_type': 'dialog'}}}
    parsed = parse_update(event)
    assert parsed == MaxInput('42', 'callback:42:cb:1', '/termsagree '+token)
    assert parse_update(copy.deepcopy(event)).event_key == parsed.event_key
    for mutate in (
        lambda e: e['message']['recipient'].update(chat_type='chat'),
        lambda e: e['callback']['user'].update(is_bot=True),
        lambda e: e['callback']['user'].update(user_id=True),
        lambda e: e['callback'].update(payload='/attack'),
        lambda e: e['callback'].update(callback_id=''),
        lambda e: e.update(message=None),
    ):
        invalid = copy.deepcopy(event)
        mutate(invalid)
        assert parse_update(invalid) is None


class MemoryStore:
    """Test replacement only: app uses the PostgreSQL Store in production."""
    def __init__(self):
        self.states = {}
    async def load(self, uid):
        return copy.deepcopy(self.states.get(uid))
    async def begin(self, uid):
        self.states[uid] = wizard.fresh()
        return await self.load(uid)
    async def transition(self, uid, old, new):
        if self.states[uid]['token'] != old['token']:
            return False
        self.states[uid] = copy.deepcopy(new)
        return True
    async def clear(self, uid):
        self.states.pop(uid, None)


async def test_application():
    store = MemoryStore()
    sent = AsyncMock()
    db = SimpleNamespace(pool=object(), reserve_max_player_id=AsyncMock(return_value=-1),
                         create_character=AsyncMock(), load_active_character=AsyncMock(return_value=None))
    env = dict(asyncio=asyncio, send=sent, _max_reply=sent, chars={}, db=db,
               _max_input_locks={}, _presence=SimpleNamespace(touch=Mock()),
               _mod=SimpleNamespace(is_banned=lambda _: False), cmds=commands,
               _ts=textsafe, _starter=starter, Character=Character, MaxInput=MaxInput,
               RACES=content.RACES, CLASSES=content.CLASSES, WORLD=content.WORLD,
               HUB_ROOM=START_ROOM, world=object(), others_in=lambda _: [],
               ui=SimpleNamespace(render_room=lambda *_: 'START_ROOM', DIR_ICONS={}),
               analytics=SimpleNamespace(track=Mock()), NameTaken=NameTaken,
               ActiveCharacterExists=ActiveCharacterExists, max_onboarding=wizard)
    tree = ast.parse(Path('bot/main.py').read_text(encoding='utf-8'))
    names = {'_max_intro', '_max_onboarding_show', '_max_onboarding_input',
             '_max_onboarding_done', '_max_onboarding_error', '_max_restore_character'}
    exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                                and n.name in names], type_ignores=[]), 'wizard route', 'exec'), env)
    handler = load_handler(env)
    async def event(text):
        await handler(MaxInput('42', 'test', text))
    with patch.object(wizard, 'Store', return_value=store):
        await event('/start')
        assert sent.await_args.kwargs['max_image'] == 'world'
        await event(click(store.states[-1], 'begin'))
        await event(click(store.states[-1], 'race human'))
        assert sent.await_args.kwargs['max_image'] == 'human'
        saved = copy.deepcopy(store.states[-1])
        await event('/start')  # Resume, do not reset selection.
        assert store.states[-1] == saved
        await event(click(saved, 'confirm'))
        await event(click(saved, 'confirm'))  # Old button cannot advance to name.
        assert store.states[-1]['step'] == 'classes'
        await event(click(store.states[-1], 'class mage'))
        assert sent.await_args.kwargs['max_image'] == 'human-mage'
        await event(click(store.states[-1], 'confirm'))
        for bad in ('x', 'Имя с пробелами', 'Админ', '[]', 'А'*21):
            await event(bad)
            db.create_character.assert_not_awaited()
            assert store.states[-1]['step'] == 'name'
        await event(click(store.states[-1], 'cancel'))
        assert store.states[-1]['step'] == 'class_preview'
        await event(click(store.states[-1], 'confirm'))
        db.create_character.side_effect = NameTaken()
        await event('Занятое')
        assert store.states[-1]['step'] == 'name'
        assert 'Имя уже занято' in sent.await_args.args[1]
        assert sent.await_args.kwargs['max_keyboard'][0][0]['text'] == 'Отмена'
        db.create_character.side_effect = None
        await event('Арден')
        ch = env['chars'][-1]
        assert (ch.race, ch.cls, ch.name) == ('human', 'mage', 'Арден')
        assert ch.room == START_ROOM and 'START_ROOM' in sent.await_args.args[1]
        assert -1 not in store.states
        created = db.create_character.await_count
        await event(click(saved, 'confirm'))
        assert db.create_character.await_count == created  # No second character.
        env['chars'].clear()
        db.load_active_character.return_value = ch
        await event('/start')
        assert env['chars'][-1] is ch and db.create_character.await_count == created
        env['chars'].clear()
        db.load_active_character.return_value = None
        await event('/create orc mage Нельзя')
        assert db.create_character.await_count == created  # Text fallback cannot bypass race rules.


async def test_media_delivery():
    client = MaxClient('test-token')
    calls = []
    class Response(_Response):
        def __init__(self, payload, status=200):
            super().__init__()
            self.payload, self.status = payload, status
        async def json(self):
            return self.payload
    responses = [Response({'code': 'attachment.not.ready'}, 400), _Response()]
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return responses.pop(0)
    client._session = SimpleNamespace(post=post)
    client.image_payload = AsyncMock(return_value={'photos': {'1': {'token': 'photo-token'}}})
    try:
        await client.send_chunk('42', 'World', image_asset='world', keyboard=nav.terms_keyboard('a'*32))
    except MaxSendError as exc:
        assert exc.retryable and exc.status == 503
    else:
        raise AssertionError('Image processing delay treated as successful delivery')
    await client.send_chunk('42', 'World', image_asset='world')
    assert calls[-1][1]['json']['attachments'][0]['type'] == 'image'
    row = dict(id=1, external_user_id='42', message_text='World', attempts=1,
               expired=False, lease_token='lease', image_asset='world', keyboard=nav.terms_keyboard('a'*32))
    worker_store = SimpleNamespace(claim=AsyncMock(return_value=row), finish=AsyncMock())
    sender = SimpleNamespace(send_chunk=AsyncMock(side_effect=TimeoutError()))
    worker = MaxOutboxWorker(worker_store, sender)
    await worker.process_one()
    assert worker_store.finish.await_args.args[1] == 'pending'
    assert sender.send_chunk.await_args.kwargs['image_asset'] == 'world'


async def test_upload_boundary():
    client = MaxClient('test-token')
    calls = []
    class Response(_Response):
        def __init__(self, value):
            super().__init__()
            self.value = value
        async def json(self):
            return self.value
    responses = [Response({'url': 'https://pu.mycdn.me/upload'}),
                 Response({'photos': {'1': {'token': 'photo-token'}}}), _Response()]
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return responses.pop(0)
    client._session = SimpleNamespace(post=post)
    path = SimpleNamespace(name='world.jpg', is_file=lambda: True,
                            stat=lambda: SimpleNamespace(st_size=12),
                            open=lambda _: io.BytesIO(b'jpeg-fixture'))
    with patch.object(wizard, 'asset_path', return_value=path):
        first = await client.image_payload('world')
        assert first == await client.image_payload('world')
        assert len(calls) == 2
        await client.send_chunk('42', 'World', image_asset='world')
    assert calls[0][0] == API_URL+'/uploads'
    assert calls[0][1]['headers']['Authorization'] == 'test-token'
    assert 'headers' not in calls[1][1]  # No bot token at the signed CDN URL.
    assert calls[1][1]['data']._fields[0][0]['name'] == 'data'
    assert all(call[1]['allow_redirects'] is False for call in calls)
    for url in ('http://pu.mycdn.me/u', 'https://127.0.0.1/u', 'https://max.ru.evil.org/u',
                'https://user:pass@max.ru/u', 'https://max.ru:8443/u'):
        blocked = MaxClient('test-token')
        seen = []
        def unsafe_post(target, **kwargs):
            seen.append(target)
            return Response({'url': url})
        blocked._session = SimpleNamespace(post=unsafe_post)
        try:
            await blocked.image_payload('world')
        except MaxSendError:
            pass
        else:
            raise AssertionError('Unsafe upload destination allowed')
        assert seen == [API_URL+'/uploads']


if __name__ == '__main__':
    test_paths()
    asyncio.run(test_application())
    asyncio.run(test_media_delivery())
    asyncio.run(test_upload_boundary())
    print('OK: MAX complete wizard, all races/classes, cancel, stale callbacks, name validation, recovery and image retry')
