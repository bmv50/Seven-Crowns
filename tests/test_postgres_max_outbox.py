"""Real PostgreSQL FIFO, recovery, fencing, partial sends and rollback."""
import asyncio
import os
import uuid
from unittest.mock import patch, AsyncMock
from types import SimpleNamespace

from engine.db import Database
from engine.max_outbox import MaxOutboxStore, MaxOutboxWorker


async def run(dsn):
    import asyncpg
    schema = 'test_max_outbox_' + uuid.uuid4().hex
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
        store = MaxOutboxStore(db.pool)
        await store.enqueue(-1, '42', 'а' * 7001)
        await store.enqueue(-2, '43', 'другой диалог')
        await db.close()
        await connect()
        store = MaxOutboxStore(db.pool)
        assert await db.pool.fetchval('SELECT count(*) FROM max_outbox') == 4
        # Two workers claim different dialogs, not adjacent chunks of one reply.
        claims = await asyncio.gather(store.claim(), store.claim())
        assert {r['external_user_id'] for r in claims} == {'42', '43'}
        first = next(r for r in claims if r['external_user_id'] == '42')
        other = next(r for r in claims if r['external_user_id'] == '43')
        assert await store.claim() is None
        await store.finish(other, 'sent')
        await store.finish(first, 'pending', 'http_429', 120)
        # Delayed head blocks only its own dialog; not the whole outbox.
        await store.enqueue(-2, '43', 'следующий')
        next_other = await store.claim()
        assert next_other['external_user_id'] == '43'
        await store.finish(next_other, 'sent')
        assert await store.claim() is None
        await db.pool.execute("UPDATE max_outbox SET next_attempt_at=now() WHERE id=$1", first['id'])
        recovered = await store.claim()
        assert recovered['id'] == first['id'] and recovered['attempts'] == 2
        assert not await store.finish(first, 'sent')  # stale lease token
        await store.finish(recovered, 'sent')
        # First chunk is ACKed; retry the second only after a network failure.
        client = SimpleNamespace(send_chunk=AsyncMock(side_effect=TimeoutError()))
        worker = MaxOutboxWorker(store, client)
        await worker.process_one()
        second_id = await db.pool.fetchval("SELECT min(id) FROM max_outbox WHERE status='pending'")
        await db.pool.execute("UPDATE max_outbox SET next_attempt_at=now() WHERE id=$1", second_id)
        client.send_chunk.side_effect = None
        await worker.process_one()
        await worker.process_one()
        assert [len(call.args[1]) for call in client.send_chunk.call_args_list] == [3500, 3500, 1]
        assert await db.pool.fetchval("SELECT count(*) FROM max_outbox WHERE status='sent'") == 5
        assert await db.pool.fetchval('SELECT count(*) FROM max_outbox WHERE message_text IS NOT NULL') == 0
        # A process crash retains a claim until the lease expires; restart recovers it.
        await store.enqueue(-1, '42', 'рестарт')
        abandoned = await store.claim()
        await db.close()
        await connect()
        store = MaxOutboxStore(db.pool)
        assert await store.claim() is None
        await db.pool.execute("UPDATE max_outbox SET lease_until=now()-interval '1 second' WHERE id=$1",
                              abandoned['id'])
        recovered = await store.claim()
        assert recovered['id'] == abandoned['id'] and recovered['attempts'] == 2
        assert not await store.finish(abandoned, 'failed')
        await store.finish(recovered, 'sent')
        # Expired payloads never go over the network.
        await store.enqueue(-1, '42', 'устарело')
        await db.pool.execute("UPDATE max_outbox SET expires_at=now()-interval '1 second' WHERE status='pending'")
        client.send_chunk.reset_mock()
        await MaxOutboxWorker(store, client).process_one()
        client.send_chunk.assert_not_awaited()
        # All chunks enqueue atomically if any INSERT fails.
        await db.pool.execute('''CREATE FUNCTION reject_outbox_probe() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.message_text='!' THEN RAISE EXCEPTION 'rollback probe'; END IF;
            RETURN NEW; END $$;
            CREATE TRIGGER outbox_probe BEFORE INSERT ON max_outbox
            FOR EACH ROW EXECUTE FUNCTION reject_outbox_probe();''')
        before = await db.pool.fetchval('SELECT count(*) FROM max_outbox')
        try:
            await store.enqueue(-1, '42', 'x' * 3500 + '!')
        except asyncpg.PostgresError:
            pass
        else:
            raise AssertionError('Expected INSERT rollback')
        assert await db.pool.fetchval('SELECT count(*) FROM max_outbox') == before
        # Concurrent replies retain their own contiguous chunk order.
        await asyncio.gather(store.enqueue(-1, '42', 'a' * 7000),
                             store.enqueue(-1, '42', 'b' * 7000))
        texts = await db.pool.fetch('SELECT message_text FROM max_outbox WHERE status=\'pending\' ORDER BY id')
        assert [r['message_text'][0] for r in texts] in (['a','a','b','b'], ['b','b','a','a'])
        await db.pool.execute("UPDATE max_outbox SET finished_at=now()-interval '8 days' WHERE status IN ('sent','failed')")
        await store.cleanup()
        assert await db.pool.fetchval('SELECT count(*) FROM max_outbox') == 4
    finally:
        await db.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


if __name__ == '__main__':
    dsn = os.getenv('TEST_DATABASE_URL')
    if dsn:
        asyncio.run(run(dsn))
        print('OK: PostgreSQL MAX outbox restart, FIFO, concurrent claims, partial delivery, lease fencing, expiry and rollback')
    else:
        print('SKIP: TEST_DATABASE_URL missing; PostgreSQL MAX outbox tests run in CI')
