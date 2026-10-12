"""Source cards, edit jobs and combat panel IDs survive real DB restarts."""
import asyncio
import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from engine.db import Database
from engine.character import Character
from engine.max_outbox import MaxOutboxStore, MaxOutboxWorker, MaxSendError


async def run(dsn):
    import asyncpg
    schema = 'test_max_panels_'+uuid.uuid4().hex
    admin = await asyncpg.connect(dsn)
    db = Database(dsn)
    factory = asyncpg.create_pool
    def scoped(*args, **kwargs):
        kwargs['server_settings'] = {'search_path':schema}
        return factory(*args, **kwargs)
    async def connect():
        with patch('engine.db.asyncpg.create_pool', side_effect=scoped):
            await db.connect()
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        await connect()
        uid = await db.reserve_max_player_id('42')
        ch = Character(uid=uid, name='Панель', race='human', cls='mage', room='cellar')
        ch.init_vitals(); ch.init_skills()
        await db.create_character(ch)
        menu = [[{'type':'message','text':'🔍 Осмотр'}]]
        key = f'{ch.generation}:{ch.room}'
        assert await db.enqueue_max_update('callback:42:source','42','/inv',source_message_id='mid.source')
        assert not await db.enqueue_max_update('callback:42:source','42','/inv',source_message_id='mid.other')
        await db.close(); await connect()
        event = await db.claim_next_max_update()
        assert event['source_message_id'] == 'mid.source'
        assert await db.claim_next_max_update() is None  # Never replay game input.
        await db.finish_max_update(event['event_key'])
        assert await db.pool.fetchval('SELECT source_message_id FROM max_inbox') is None
        store = MaxOutboxStore(db.pool)
        await store.enqueue(uid,'42','Сумка',keyboard=menu,edit_message_id=event['source_message_id'],
                            panel_key=key,generation=ch.generation)
        await db.close(); await connect()
        store = MaxOutboxStore(db.pool)
        client = SimpleNamespace(send_chunk=AsyncMock(return_value='mid.source'))
        worker = MaxOutboxWorker(store,client)
        assert await worker.process_one()
        assert client.send_chunk.await_args.kwargs == {'keyboard':menu,'edit_message_id':'mid.source'}
        assert await db.pool.fetchval('SELECT delivered_message_id FROM max_outbox ORDER BY id LIMIT 1') == 'mid.source'
        # The first hit targets its source card; subsequent ticks reuse its saved ID.
        await store.enqueue_combat(uid,'42','Удар','HP 50/100',key,ch.generation,urgent=True,keyboard=menu,
                                   edit_message_id='mid.source')
        await db.close(); await connect()
        store = MaxOutboxStore(db.pool); worker = MaxOutboxWorker(store,client)
        assert await worker.process_one()
        assert client.send_chunk.await_args.kwargs['edit_message_id'] == 'mid.source'
        await store.enqueue(uid,'42','Сумка отдельно',keyboard=menu,panel_key=key,generation=ch.generation)
        client.send_chunk.return_value = 'mid.bag'
        await worker.process_one()
        client.send_chunk.return_value = 'mid.source'
        await store.enqueue_combat(uid,'42','Следующий удар','HP 45/100',key,ch.generation,urgent=True,keyboard=menu)
        await worker.process_one()
        assert client.send_chunk.await_args.kwargs['edit_message_id'] == 'mid.source'
        # Callback selects its exact source even when another card was active before it.
        await store.enqueue(uid,'42','Противник',keyboard=menu,edit_message_id='mid.clicked',
                            panel_key=key,generation=ch.generation)
        client.send_chunk.return_value = 'mid.clicked'
        await worker.process_one()
        await store.enqueue_combat(uid,'42','Заклинание','HP 40/100',key,ch.generation,urgent=True,keyboard=menu,
                                   edit_message_id='mid.clicked')
        await worker.process_one()
        assert client.send_chunk.await_args.kwargs['edit_message_id'] == 'mid.clicked'
        await store.enqueue_combat(uid,'42','Ответ моба','HP 35/100',key,ch.generation,urgent=True,keyboard=menu)
        await worker.process_one()
        assert client.send_chunk.await_args.kwargs['edit_message_id'] == 'mid.clicked'
        # Deleted source: force-new persists before fallback and survives a timeout/restart.
        await store.enqueue_combat(uid,'42','Карточка','HP 38/100',key,ch.generation,
                                   urgent=True,keyboard=menu,edit_message_id='mid.deleted')
        client.send_chunk.side_effect = [MaxSendError(404),TimeoutError()]
        await worker.process_one()
        failed = await db.pool.fetchrow("SELECT * FROM max_outbox WHERE status='pending'")
        assert failed['edit_message_id'] == ''
        assert await db.pool.fetchval("SELECT count(*) FROM max_outbox WHERE status='pending'") == 1
        await db.close(); await connect()
        store = MaxOutboxStore(db.pool); worker = MaxOutboxWorker(store,client)
        await db.pool.execute("UPDATE max_outbox SET next_attempt_at=now() WHERE status='pending'")
        client.send_chunk.side_effect = None; client.send_chunk.return_value = 'mid.replacement'
        assert await worker.process_one()
        assert 'edit_message_id' not in client.send_chunk.await_args.kwargs
        await store.enqueue_combat(uid,'42','Новый удар','HP 30/100',key,ch.generation,urgent=True,keyboard=menu)
        await worker.process_one()
        assert client.send_chunk.await_args.kwargs['edit_message_id'] == 'mid.replacement'
        # Another dialog or room has no permission to reuse this panel ID.
        probe = {'uid':uid,'external_user_id':'43','panel_key':key,'id':99999}
        assert await store.active_panel(probe) is None
        probe.update(external_user_id='42',panel_key=f'{ch.generation}:village')
        assert await store.active_panel(probe) is None
        # Large descriptions remain complete new-message chunks, never self-overwritten edits.
        await store.enqueue(uid,'42','a'*7001,keyboard=menu,edit_message_id='mid.source',
                            panel_key=key,generation=ch.generation)
        pending = await db.pool.fetch("SELECT * FROM max_outbox WHERE status='pending' ORDER BY id")
        assert len(pending) == 3 and all(r['edit_message_id'] is None for r in pending)
        assert [r['panel_key'] for r in pending] == [None,None,key]
        assert ''.join(r['message_text'] for r in pending) == 'a'*7001
        while await worker.process_one():
            pass
        # A stale lease cannot retarget or record a successful delivery.
        await store.enqueue(uid,'42','Старая аренда',keyboard=menu,edit_message_id='mid.source')
        old = await store.claim()
        await db.pool.execute("UPDATE max_outbox SET lease_until=now()-interval '1 second' WHERE id=$1",old['id'])
        recovered = await store.claim()
        assert not await store.abandon_edit(old)
        assert not await store.finish(old,'sent',delivered_message_id='mid.wrong')
        assert await store.finish(recovered,'sent')
        # Rollback switch ignores queued edit jobs without requiring destructive DB rollback.
        await store.enqueue(uid,'42','Откат',keyboard=menu,edit_message_id='mid.source',
                            panel_key=key,generation=ch.generation)
        with patch.dict('os.environ',{'MAX_EDIT_CARDS':'0'}):
            await worker.process_one()
        assert 'edit_message_id' not in client.send_chunk.await_args.kwargs
        # Hero reset fences a queued panel even when its old message still exists.
        await store.enqueue(uid,'42','Старый герой',keyboard=menu,edit_message_id='mid.source',
                            panel_key=key,generation=ch.generation)
        await db.reset_character(uid,ch.generation)
        client.send_chunk.reset_mock()
        await worker.process_one()
        client.send_chunk.assert_not_awaited()
    finally:
        await db.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


if __name__ == '__main__':
    dsn = os.getenv('TEST_DATABASE_URL')
    if dsn:
        try:
            asyncio.run(run(dsn))
        except AssertionError as exc:
            import traceback
            frame = traceback.extract_tb(exc.__traceback__)[-1]
            raise AssertionError(f'MAX panel PostgreSQL check, line {frame.lineno}: {frame.line}') from exc
        print('OK: PostgreSQL MAX source/edit restart, combat panel reuse, persisted fallback, lease fencing, chunk integrity and rollback')
    else:
        print('SKIP: TEST_DATABASE_URL missing; PostgreSQL MAX panel tests run in CI')
