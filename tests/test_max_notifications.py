"""Application routing: queue acceptance never masquerades as delivery."""
import ast
import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from engine import notify
from engine.character import Character


async def run():
    notify.ENABLED = True
    ch = Character(uid=-1, name='Тестер', cls='warrior', race='human')
    queue = AsyncMock(return_value=True)
    telegram = SimpleNamespace(deliver=AsyncMock(return_value='sent'))
    db = SimpleNamespace(pool=object(), max_external_user_id=AsyncMock(return_value='42'))
    env = dict(bot=object(), _notify=notify, _max_client=object(), db=db, chars={ch.uid: ch},
               _time_mod=time, _notification_delivery=telegram,
               MaxOutboxStore=lambda _: SimpleNamespace(enqueue_notification=queue))
    tree = ast.parse(Path('bot/main.py').read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == '_notify_deliver')
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'routing', 'exec'), env)
    deliver = env['_notify_deliver']
    assert await deliver(-1, 'world_boss', 'босс', expires_at=123, event_key='boss:1') == 'queued'
    queue.assert_awaited_once_with(-1, '42', 'босс', 'world_boss', ch.generation,
                                  expires_at=123, event_key='boss:1')
    assert 'notify_quota' not in ch.flags
    telegram.deliver.assert_not_awaited()
    queue.return_value = False
    assert await deliver(-1, 'world_event', 'нет согласия') == 'drop'
    assert await deliver(10, 'world_event', 'Telegram') == 'sent'
    telegram.deliver.assert_awaited_once()
    env['bot'] = None
    assert await deliver(10, 'world_event', 'Telegram disabled') == 'drop'
    telegram.deliver.assert_awaited_once()
    env['_max_client'] = None
    before = queue.await_count
    assert await deliver(-1, 'world_event', 'выключено') == 'drop'
    assert queue.await_count == before
    # MAX quiet-hour deferral is delegated to PostgreSQL, not kept in RAM.
    notify.clear()
    notify.set_opt_in(ch, True)
    notify.emit(-1, 'daily_reset', 'ежедневка')
    ready = notify.due(0, {-1: ch})
    assert len(ready) == 1 and not notify.pending()
    # Unknown notification policy cannot accidentally use the ordinary-response path.
    from engine.max_outbox import MaxOutboxWorker
    row = dict(id=1, category='world_event', expired=False, attempts=1)
    store = SimpleNamespace(claim=AsyncMock(return_value=row), finish=AsyncMock())
    client = SimpleNamespace(send_chunk=AsyncMock())
    await MaxOutboxWorker(store, client).process_one()
    client.send_chunk.assert_not_awaited()
    store.finish.assert_awaited_once_with(row, 'failed', 'notification_policy_missing')


if __name__ == '__main__':
    asyncio.run(run())
    print('OK: MAX push durable routing, no premature quota, Telegram isolation and policy fail-closed')
