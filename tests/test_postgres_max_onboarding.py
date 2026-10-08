"""Real PostgreSQL wizard persistence, revision fencing and durable media delivery."""
import asyncio
import json
import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from engine.db import Database
from engine import max_onboarding as wizard
from engine.character import Character
from engine.max_outbox import MaxOutboxStore, MaxOutboxWorker


async def run(dsn):
    import asyncpg
    schema = 'test_max_onboarding_' + uuid.uuid4().hex
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
        other = await db.reserve_max_player_id('43')
        store = wizard.Store(db.pool)
        state = await store.begin(uid)
        assert await store.load(other) is None
        assert await store.load(uid) == state
        next_state = wizard.advance(state, f"/onboard {state['token']} begin")
        outcomes = await asyncio.gather(store.transition(uid, state, next_state),
                                        store.transition(uid, state, next_state))
        assert sorted(outcomes) == [False, True]
        assert not await store.transition(other, state, next_state)
        for action in ('race human', 'confirm', 'class mage', 'confirm'):
            old = await store.load(uid)
            new = wizard.advance(old, f"/onboard {old['token']} {action}")
            assert await store.transition(uid, old, new)
        named = await store.load(uid)
        assert named['step'] == 'name'
        await db.close()
        await connect()
        store = wizard.Store(db.pool)
        assert await store.load(uid) == named
        assert not await store.transition(uid, state, next_state)
        ch = Character(uid=uid, name='Арден', race='human', cls='mage')
        ch.init_vitals()
        ch.init_skills()
        await db.create_character(ch)
        recovered = await db.load_active_character(uid)
        assert recovered.name == ch.name and recovered.generation == ch.generation
        assert await db.load_active_character(other) is None
        await store.clear(uid)
        assert await store.load(uid) is None
        card, menu, image = wizard.screen(wizard.fresh('class_preview', 'human', 'mage'))
        outbox = MaxOutboxStore(db.pool)
        assert await outbox.enqueue(uid, '42', 'x'*7001, keyboard=menu, image_asset=image) == 3
        rows = await db.pool.fetch('SELECT * FROM max_outbox ORDER BY id')
        assert [r['image_asset'] for r in rows] == [None, None, 'human-mage']
        await db.close()
        await connect()
        outbox = MaxOutboxStore(db.pool)
        sender = SimpleNamespace(send_chunk=AsyncMock())
        worker = MaxOutboxWorker(outbox, sender)
        await worker.process_one()
        await worker.process_one()
        sender.send_chunk.side_effect = TimeoutError()
        await worker.process_one()
        assert sender.send_chunk.await_args.kwargs == {'keyboard': menu, 'image_asset': image}
        row = await db.pool.fetchrow('SELECT * FROM max_outbox WHERE id=$1', rows[-1]['id'])
        assert row['status'] == 'pending' and row['image_asset'] == image
        sender.send_chunk.side_effect = None
        await db.pool.execute('UPDATE max_outbox SET next_attempt_at=now()')
        await worker.process_one()
        assert await db.pool.fetchval('SELECT count(*) FROM max_outbox WHERE image_asset IS NOT NULL') == 0
        # The same durable media path handles allowlisted location previews.
        from engine.max_media import PREVIEW_ROOMS
        for room in sorted(PREVIEW_ROOMS):
            key = 'room:'+room
            await outbox.enqueue(uid, '42', 'Location:'+room, image_asset=key)
            await worker.process_one()
            assert sender.send_chunk.await_args.kwargs == {'image_asset': key}
        assert await db.pool.fetchval('SELECT count(*) FROM max_outbox WHERE image_asset IS NOT NULL') == 0
        # Bad paths must be rejected before inserting any delivery.
        from engine import max_map
        map_key = max_map.image_key('village', ['village', 'cellar'])
        map_menu = max_map.keyboard(ch, ['village', 'cellar'])
        await outbox.enqueue(uid, '42', 'Map snapshot', keyboard=map_menu, image_asset=map_key)
        await db.close()
        await connect()
        outbox = MaxOutboxStore(db.pool)
        worker = MaxOutboxWorker(outbox, sender)
        assert await worker.process_one()
        assert sender.send_chunk.await_args.kwargs == {'keyboard': map_menu, 'image_asset': map_key}
        assert max_map.snapshot(map_key) == ('village', ['village', 'cellar'])
        from engine import item_art
        item_key = item_art.image_key('железный_меч#purple#42')
        await outbox.enqueue(uid, '42', 'Item snapshot', image_asset=item_key)
        await db.close()
        await connect()
        outbox = MaxOutboxStore(db.pool)
        worker = MaxOutboxWorker(outbox, sender)
        await worker.process_one()
        assert sender.send_chunk.await_args.kwargs == {'image_asset': item_key}
        assert item_art.snapshot(item_key) == 'железный_меч#purple#42'
        before = await db.pool.fetchval('SELECT count(*) FROM max_outbox')
        try:
            await outbox.enqueue(uid, '42', 'unsafe', image_asset='../.env')
        except ValueError:
            pass
        else:
            raise AssertionError('Arbitrary media path accepted')
        assert await db.pool.fetchval('SELECT count(*) FROM max_outbox') == before
        store = wizard.Store(db.pool)
        await store.begin(uid)
        await db.pool.execute("UPDATE max_onboarding SET updated_at=now()-interval '8 days'")
        assert await store.load(uid) is None
        await outbox.cleanup()
        assert await db.pool.fetchval('SELECT count(*) FROM max_onboarding') == 0
        # Callback events are claimed once just like text events.
        assert await db.enqueue_max_update('callback:42:cb1', '42', '/termsagree '+'a'*32)
        assert not await db.enqueue_max_update('callback:42:cb1', '42', '/termsagree '+'a'*32)
        assert (await db.claim_next_max_update())['event_key'] == 'callback:42:cb1'
        assert await db.claim_next_max_update() is None
        await db.finish_max_update('callback:42:cb1')
    finally:
        await db.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


if __name__ == '__main__':
    dsn = os.environ.get('TEST_DATABASE_URL')
    if not dsn:
        print('SKIP: TEST_DATABASE_URL missing; PostgreSQL MAX onboarding tests run in CI')
    else:
        asyncio.run(run(dsn))
        print('OK: PostgreSQL MAX onboarding restart, CAS, identity, create recovery, media retry, cleanup and dedup')
