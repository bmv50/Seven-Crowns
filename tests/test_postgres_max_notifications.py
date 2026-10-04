"""Real policy at delivery, atomic quotas, restart, reset and retry safety."""
import asyncio
import json
import os
import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from engine import notify
from engine.character import Character
from engine.db import Database
from engine.max_outbox import MaxOutboxStore, MaxOutboxWorker, MaxSendError
from engine.max_notifications import MaxNotificationSender


async def run(dsn):
    import asyncpg
    notify.ENABLED = True
    schema = 'test_max_push_' + uuid.uuid4().hex
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
        ch = Character(uid=uid, name='Уведомления', cls='warrior', race='human')
        ch.init_vitals()
        ch.init_skills()
        ch.flags['notify'] = {'quiet_off': True, 'limit': 1}
        await db.create_character(ch)
        chars = {uid: ch}
        client = SimpleNamespace(send_chunk=AsyncMock())
        telemetry = Mock()
        now = [time.time()]
        store = MaxOutboxStore(db.pool)
        def make_worker():
            sender = MaxNotificationSender(db, store, client, chars.get, telemetry, clock=lambda: now[0])
            return MaxOutboxWorker(store, client, sender)
        async def enqueue(cat='world_event', text='событие', key=None):
            return await store.enqueue_notification(uid, '42', text, cat, ch.generation, event_key=key)
        async def flags():
            return json.loads(await db.pool.fetchval('SELECT flags FROM characters WHERE uid=$1', uid))
        assert not await enqueue()  # preferences alone are not consent
        await db.set_player_setting(ch, 'push', 'on')
        assert await enqueue('daily_reset', key='daily:1')
        assert not await enqueue('daily_reset', key='daily:1')
        assert await enqueue('world_event')
        assert await enqueue('auction_sold')
        assert 'notify_quota' not in await flags()
        worker = make_worker()
        for _ in range(3):
            assert await worker.process_one()
        assert client.send_chunk.await_count == 2
        assert (await flags())['notify_quota']['count'] == 1
        assert ch.flags['notify_quota']['count'] == 1
        assert await db.pool.fetchval('SELECT count(*) FROM notify_log WHERE ok') == 2
        await db.save(ch)  # regular save must retain committed quota
        await db.close()
        await connect()
        store = MaxOutboxStore(db.pool)
        ch = (await db.load_all())[uid]
        chars[uid] = ch
        assert ch.flags['notify_quota']['count'] == 1
        # Current settings, not settings at enqueue time.
        assert await enqueue('auction_sold')
        await db.set_player_setting(ch, 'push', 'off')
        before = client.send_chunk.await_count
        await make_worker().process_one()
        assert client.send_chunk.await_count == before
        await db.set_player_setting(ch, 'push', 'on')
        assert await enqueue('auction_sold')
        await db.set_player_setting(ch, 'auction_sold', 'off')
        await make_worker().process_one()
        assert client.send_chunk.await_count == before
        await db.set_player_setting(ch, 'auction_sold', 'on')
        assert await enqueue('auction_sold')
        notify.ENABLED = False
        await make_worker().process_one()
        assert client.send_chunk.await_count == before
        notify.ENABLED = True
        assert await enqueue('auction_sold')
        await db.pool.execute("UPDATE max_outbox SET expires_at=now()-interval '1 second' WHERE status='pending'")
        await make_worker().process_one()
        assert client.send_chunk.await_count == before
        await db.set_player_setting(ch, 'world_event', 'off')
        assert not await enqueue('world_event')
        await db.set_player_setting(ch, 'world_event', 'on')
        # A reset invalidates notifications from the previous hero generation.
        assert await enqueue('auction_sold')
        old_generation = ch.generation
        await db.reset_character(uid, ch.generation)
        ch = Character(uid=uid, name='НовыйГерой', cls='warrior', race='human')
        ch.init_vitals()
        ch.init_skills()
        ch.flags['notify'] = {'push_enabled': True, 'quiet_off': True, 'limit': 5}
        await db.create_character(ch)
        chars[uid] = ch
        assert ch.generation != old_generation
        await make_worker().process_one()
        assert client.send_chunk.await_count == before
        # Quiet-hour deferral survives restart and doesn't consume attempts/quota.
        await db.set_player_setting(ch, 'quiet', 'on')
        await db.set_player_setting(ch, 'tz', '0')
        now[0] = int(time.time()) // 86400 * 86400 - 3600  # simulated 23:00 UTC (quiet starts at 23)
        assert await enqueue('daily_reset')
        await make_worker().process_one()
        deferred = await db.pool.fetchrow("SELECT * FROM max_outbox WHERE status='pending'")
        assert deferred is not None, 'Quiet-hour notification was not deferred at 23:00'
        assert deferred['last_error'] == 'quiet_hours' and deferred['attempts'] == 0
        assert 'notify_quota' not in await flags()
        # A deferred push must not block answers to commands in this dialog.
        await store.enqueue(uid, '42', 'ответ на команду')
        response = await store.claim()
        assert response['category'] is None and response['message_text'] == 'ответ на команду'
        await store.finish(response, 'sent')
        await db.close()
        await connect()
        store = MaxOutboxStore(db.pool)
        assert await db.pool.fetchval("SELECT attempts FROM max_outbox WHERE id=$1", deferred['id']) == 0
        now[0] += 12 * 3600  # simulated 11:00
        await db.pool.execute('UPDATE max_outbox SET next_attempt_at=now() WHERE id=$1', deferred['id'])
        await make_worker().process_one()
        assert (await flags())['notify_quota']['count'] == 1
        # Failure doesn't consume quota; successful retry counts once.
        await db.set_player_setting(ch, 'quiet', 'off')
        now[0] = time.time()
        assert await enqueue('world_event')
        client.send_chunk.side_effect = TimeoutError()
        await make_worker().process_one()
        assert (await flags())['notify_quota']['count'] == 1
        await db.pool.execute("UPDATE max_outbox SET next_attempt_at=now() WHERE status='pending'")
        client.send_chunk.side_effect = None
        await make_worker().process_one()
        assert (await flags())['notify_quota']['count'] == 2
        # DB failure after HTTP ACK must roll back sent marker and quota together.
        assert await enqueue('world_event')
        await db.pool.execute('''CREATE FUNCTION reject_push_probe() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.ok THEN RAISE EXCEPTION 'rollback probe'; END IF; RETURN NEW; END $$;
            CREATE TRIGGER push_probe BEFORE INSERT ON notify_log
            FOR EACH ROW EXECUTE FUNCTION reject_push_probe();''')
        try:
            await make_worker().process_one()
        except asyncpg.PostgresError:
            pass
        else:
            raise AssertionError('Expected post-ACK transaction rollback')
        assert (await flags())['notify_quota']['count'] == 2 and ch.flags['notify_quota']['count'] == 2
        await db.pool.execute('DROP TRIGGER push_probe ON notify_log')
        await db.pool.execute("UPDATE max_outbox SET lease_until=now()-interval '1 second' WHERE status='processing'")
        telemetry.side_effect = RuntimeError('analytics unavailable')
        await make_worker().process_one()
        assert (await flags())['notify_quota']['count'] == 3
        # 403 suppresses future push until explicit renewed consent.
        assert await enqueue('auction_sold')
        client.send_chunk.side_effect = MaxSendError(403)
        await make_worker().process_one()
        assert await db.pool.fetchval('SELECT notify_blocked FROM characters WHERE uid=$1', uid)
        assert not await enqueue('auction_sold')
        await db.set_player_setting(ch, 'push', 'on')
        assert not await db.pool.fetchval('SELECT notify_blocked FROM characters WHERE uid=$1', uid)
        assert await enqueue('auction_sold', 'x' * 8000)
        payload = await db.pool.fetchval("SELECT message_text FROM max_outbox WHERE status='pending'")
        assert len(payload) <= 3500 and 'сокращено' in payload
        client.send_chunk.side_effect = None
        await make_worker().process_one()
        assert (await flags())['notify_quota']['count'] == 3  # auctions stay off-quota
        # A save already waiting during delivery serializes the committed quota.
        assert await enqueue('world_event')
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow_send(*_):
            entered.set()
            await release.wait()
        client.send_chunk.side_effect = slow_send
        delivery = asyncio.create_task(make_worker().process_one())
        await asyncio.wait_for(entered.wait(), 5)
        saving = asyncio.create_task(db.save(ch))
        await asyncio.sleep(0)
        assert not saving.done()
        release.set()
        await asyncio.gather(delivery, saving)
        assert (await flags())['notify_quota']['count'] == 4
        client.send_chunk.side_effect = None
        # Concurrent workers cannot exceed the last daily slot.
        assert await enqueue('world_event')
        assert await enqueue('world_event')
        await asyncio.gather(make_worker().process_one(), make_worker().process_one())
        while await make_worker().process_one():
            pass
        assert (await flags())['notify_quota']['count'] == 5
    finally:
        await db.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


if __name__ == '__main__':
    dsn = os.getenv('TEST_DATABASE_URL')
    if dsn:
        asyncio.run(run(dsn))
        print('OK: PostgreSQL MAX push consent, durable quota, retry, rollback, quiet restart, reset, priority and blocking')
    else:
        print('SKIP: TEST_DATABASE_URL missing; PostgreSQL MAX push tests run in CI')
