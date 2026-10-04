"""Atomic keyboard chunks, restart recovery, retry retention and data cleanup."""
import asyncio
import json
import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from engine.db import Database
from engine.max_navigation import keyboard
from engine.max_outbox import MaxOutboxStore, MaxOutboxWorker


async def run(dsn):
    import asyncpg
    schema = 'test_max_navigation_' + uuid.uuid4().hex
    admin = await asyncpg.connect(dsn)
    db = Database(dsn)
    factory = asyncpg.create_pool
    def scoped(*args, **kwargs):
        kwargs['server_settings'] = {'search_path': schema}
        return factory(*args, **kwargs)
    async def connect():
        with patch('engine.db.asyncpg.create_pool', side_effect=scoped):
            await db.connect()
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        await connect()
        uid = await db.reserve_max_player_id('42')
        menu = keyboard(None, {})
        expected = json.loads(json.dumps(menu))
        store = MaxOutboxStore(db.pool)
        assert await store.enqueue(uid, '42', 'a'*7001, keyboard=menu) == 3
        menu[0][0]['text'] = 'modified after enqueue'
        await db.close()
        await connect()
        store = MaxOutboxStore(db.pool)
        rows = await db.pool.fetch('SELECT * FROM max_outbox ORDER BY id')
        assert [r['keyboard'] is None for r in rows] == [True, True, False]
        assert json.loads(rows[-1]['keyboard']) == expected
        client = SimpleNamespace(send_chunk=AsyncMock())
        worker = MaxOutboxWorker(store, client)
        assert await worker.process_one()
        assert await worker.process_one()
        assert all(not call.kwargs for call in client.send_chunk.await_args_list)
        client.send_chunk.side_effect = TimeoutError()
        assert await worker.process_one()
        assert client.send_chunk.await_args.kwargs['keyboard'] == expected
        last = await db.pool.fetchrow('SELECT * FROM max_outbox WHERE id=$1', rows[-1]['id'])
        assert last['status'] == 'pending' and json.loads(last['keyboard']) == expected
        await db.pool.execute('UPDATE max_outbox SET next_attempt_at=now()')
        client.send_chunk.side_effect = None
        assert await worker.process_one()
        assert client.send_chunk.await_args.kwargs['keyboard'] == expected
        assert await db.pool.fetchval('SELECT count(*) FROM max_outbox WHERE keyboard IS NOT NULL') == 0
        assert await db.pool.fetchval("SELECT count(*) FROM max_outbox WHERE status='sent'") == 3
        await db.pool.execute('DELETE FROM max_outbox')
        # Failure on final chunk rolls back earlier chunks, too.
        await db.pool.execute('''CREATE FUNCTION reject_keyboard() RETURNS trigger AS $$
            BEGIN IF NEW.keyboard IS NOT NULL THEN RAISE EXCEPTION 'test keyboard failure'; END IF;
            RETURN NEW; END; $$ LANGUAGE plpgsql;
            CREATE TRIGGER reject_keyboard BEFORE INSERT ON max_outbox
            FOR EACH ROW EXECUTE FUNCTION reject_keyboard();''')
        try:
            await store.enqueue(uid, '42', 'b'*7001, keyboard=expected)
        except asyncpg.PostgresError:
            pass
        else:
            raise AssertionError('Injected database failure ignored')
        assert await db.pool.fetchval('SELECT count(*) FROM max_outbox') == 0
        await db.pool.execute('DROP TRIGGER reject_keyboard ON max_outbox')
        await store.enqueue(uid, '42', 'plain response')
        plain = await store.claim()
        assert plain['keyboard'] is None
        await store.finish(plain, 'sent')
        await store.enqueue(uid, '42', 'keyboard', keyboard=expected)
        claimed = await store.claim()
        await store.finish(claimed, 'failed', 'permanent')
        assert await db.pool.fetchval('SELECT keyboard FROM max_outbox WHERE id=$1', claimed['id']) is None
    finally:
        await db.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


if __name__ == '__main__':
    dsn = os.getenv('TEST_DATABASE_URL')
    if dsn:
        asyncio.run(run(dsn))
        print('OK: PostgreSQL MAX keyboard chunk atomicity, restart, retry retention, cleanup and rollback')
    else:
        print('SKIP: TEST_DATABASE_URL missing; PostgreSQL MAX navigation tests run in CI')
