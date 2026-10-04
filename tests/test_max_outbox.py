"""Retry decisions, chunk isolation, cancellation and the gameplay boundary."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.max_transport import MaxClient, MaxSendError
from engine.max_outbox import MAX_ATTEMPTS, MaxOutboxWorker, text_parts


def row(**changes):
    result = dict(id=1, external_user_id='42', message_text='Ответ', attempts=1,
                  expired=False, lease_token='claim')
    result.update(changes)
    return result


async def worker_case(error=None, **changes):
    claimed = row(**changes)
    store = SimpleNamespace(claim=AsyncMock(return_value=claimed), finish=AsyncMock())
    client = SimpleNamespace(send_chunk=AsyncMock(side_effect=error))
    assert await MaxOutboxWorker(store, client).process_one()
    return store, client


async def test_worker():
    store, client = await worker_case()
    store.finish.assert_awaited_once_with(row(), 'sent')
    client.send_chunk.assert_awaited_once_with('42', 'Ответ')
    for error in (TimeoutError(), MaxSendError(500), MaxSendError(429, 120)):
        store, _ = await worker_case(error)
        args = store.finish.call_args.args
        assert args[1] == 'pending'
        assert args[3] >= (120 if isinstance(error, MaxSendError) and error.status == 429 else 2)
    for error in (MaxSendError(400), MaxSendError(401), MaxSendError(403), ValueError()):
        store, _ = await worker_case(error)
        assert store.finish.call_args.args[1] == 'failed'
    store, _ = await worker_case(TimeoutError(), attempts=MAX_ATTEMPTS)
    assert store.finish.call_args.args[1] == 'failed'
    for changes in ({'expired': True}, {'attempts': MAX_ATTEMPTS + 1}):
        store, client = await worker_case(**changes)
        client.send_chunk.assert_not_awaited()
        assert store.finish.call_args.args[1] == 'failed'
    store = SimpleNamespace(claim=AsyncMock(return_value=row()), finish=AsyncMock())
    client = SimpleNamespace(send_chunk=AsyncMock(side_effect=asyncio.CancelledError()))
    try:
        await MaxOutboxWorker(store, client).process_one()
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError('Cancellation swallowed')
    store.finish.assert_not_awaited()
    store.claim.return_value = None
    assert not await MaxOutboxWorker(store, client).process_one()


class Response:
    def __init__(self, status, payload=None, retry='0'):
        self.status, self.payload = status, payload
        self.headers = {'Retry-After': retry}
    async def __aenter__(self):
        return self
    async def __aexit__(self, *_):
        pass
    async def json(self):
        return self.payload


async def test_http():
    for status, payload, retry, expected in (
        (429, None, '120', 429), (403, None, 'nan', 403),
        (500, None, 'invalid', 500), (200, {'success': False}, '0', 502),
        (200, {'message': {}}, '0', 502),
        (200, {'message': {'body': {'mid': 'm-1'}}}, '0', None),
    ):
        client = MaxClient('TOKEN_MUST_NOT_LEAK')
        calls = []
        def post(url, **kwargs):
            calls.append(kwargs)
            return Response(status, payload, retry)
        client._session = SimpleNamespace(post=post)
        try:
            await client.send_chunk('42', 'текст')
        except MaxSendError as error:
            assert error.status == expected
            assert 'TOKEN_MUST_NOT_LEAK' not in str(error)
            assert error.retry_after == (120 if status == 429 else 0)
        else:
            assert expected is None
        assert len(calls) == 1  # no hidden retry inside transport
        assert '42' in client._last_sent  # failed requests are throttled too
    assert len(text_parts('*' + 'a' * 7001 + '*')) == 3
    assert ''.join(text_parts('*' + 'a' * 7001 + '*')) == 'a' * 7001
    assert text_parts('') == []


async def test_send_boundary():
    source = Path('bot/main.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    send = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'send')
    queued = AsyncMock()
    db = SimpleNamespace(pool=object(), max_external_user_id=AsyncMock(return_value='42'))
    client = SimpleNamespace(send_user=AsyncMock())
    ns = dict(db=db, _max_client=client,
              MaxOutboxStore=lambda pool: SimpleNamespace(enqueue=queued))
    exec(compile(ast.Module(body=[send], type_ignores=[]), 'send', 'exec'), ns)
    await ns['send'](-1, 'награда')
    queued.assert_awaited_once_with(-1, '42', 'награда')
    client.send_user.assert_not_awaited()
    queued.side_effect = RuntimeError('DB unavailable')
    try:
        await ns['send'](-1, 'ответ')
    except RuntimeError:
        pass
    else:
        raise AssertionError('Queue persistence failure silently discarded')
    # The delivery worker has no route back into the input handler/game loop.
    assert '_max_handle_input' not in Path('engine/max_outbox.py').read_text(encoding='utf-8')
    stop = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == '_stop_max_workers')
    tasks = [asyncio.create_task(asyncio.sleep(10)) for _ in range(2)]
    workers = {'max_inbox_worker': {'task': tasks[0]},
               'max_outbox_worker': {'task': tasks[1]}, 'other': {'task': None}}
    ns.update(asyncio=asyncio, _WORKERS=workers)
    exec(compile(ast.Module(body=[stop], type_ignores=[]), 'shutdown', 'exec'), ns)
    await ns['_stop_max_workers']()
    assert all(t.cancelled() for t in tasks)
    assert list(workers) == ['other']


if __name__ == '__main__':
    asyncio.run(test_worker())
    asyncio.run(test_http())
    asyncio.run(test_send_boundary())
    print('OK: MAX outbox retries, permanent errors, expiry, cancellation, HTTP ACK and gameplay isolation')
