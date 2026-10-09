"""Durable bounded combat batches, frame boundaries and no edits after claim."""
import asyncio
import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from engine.db import Database
from engine.character import Character
from engine.max_outbox import MaxOutboxStore, MaxOutboxWorker


async def run(dsn):
    import asyncpg
    schema = 'test_max_combat_' + uuid.uuid4().hex
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
        ch = Character(uid=uid, name='БоевыеСводки', cls='warrior', race='human')
        ch.init_vitals()
        ch.init_skills()
        await db.create_character(ch)
        store = MaxOutboxStore(db.pool)
        async def combat(text, snapshot='❤️ Вы: 100/100', key='room', urgent=False, generation=None):
            return await store.enqueue_combat(uid, '42', text, snapshot, key,
                ch.generation if generation is None else generation, urgent)
        async def rows():
            return await db.pool.fetch('SELECT * FROM max_outbox ORDER BY id')
        async def clear():
            await db.pool.execute('DELETE FROM max_outbox')
        # Buttons survive batching and a worker restart in the existing JSON
        # column; presentation retries cannot re-run a hit or consumable action.
        from engine import max_ui, max_encounters
        from engine.world import World
        world = World()
        ch.room = 'cellar'
        await db.save(ch)
        mob = world.living_in(ch.room)[0]
        menu = max_ui.with_back(max_encounters.mob_keyboard(ch, mob))
        assert await store.enqueue_combat(uid, '42', 'кнопки боя', '❤️ 100/100', 'buttons',
                                          ch.generation, urgent=True, keyboard=menu)
        await db.close()
        await connect()
        store = MaxOutboxStore(db.pool)
        sender = SimpleNamespace(send_chunk=AsyncMock())
        worker = MaxOutboxWorker(store, sender)
        assert await worker.process_one()
        assert sender.send_chunk.await_args.kwargs == {'keyboard': menu}
        await clear()
        assert await combat('удар игрока')
        first = (await rows())[0]
        assert first['next_attempt_at'] > first['created_at'] and first['combat_open']
        assert await combat('удар моба', '❤️ Вы: 80/100')
        merged = (await rows())[0]
        assert merged['id'] == first['id'] and merged['next_attempt_at'] == first['next_attempt_at']
        assert merged['combat_log'] == 'удар игрока\nудар моба'
        assert '80/100' in merged['message_text'] and '100/100' not in merged['message_text']
        await db.close()
        await connect()
        store = MaxOutboxStore(db.pool)
        assert (await rows())[0]['combat_log'] == merged['combat_log']
        # Terminal/command response releases the earlier batch atomically.
        await store.enqueue(uid, '42', 'Победа! +10 опыта, +5 монет')
        assert await combat('новый бой')
        pending = await rows()
        assert len(pending) == 3 and pending[0]['combat_open'] is False
        claimed = await store.claim()
        assert claimed['id'] == first['id']
        await store.finish(claimed, 'sent')
        assert (await rows())[0]['combat_log'] is None
        claimed = await store.claim()
        assert 'Победа!' in claimed['message_text']
        await store.finish(claimed, 'sent')
        assert (await rows())[2]['combat_log'] == 'новый бой'
        await clear()
        # Concurrent hits merge without dropping/duplicating any log entry.
        await asyncio.gather(*(combat(f'удар:{i}') for i in range(8)))
        concurrent = await rows()
        assert len(concurrent) == 1
        assert set(concurrent[0]['combat_log'].splitlines()) == {f'удар:{i}' for i in range(8)}
        assert len(concurrent[0]['combat_log'].splitlines()) == 8
        # Urgent HP warning closes the frame without waiting for the two-second window.
        assert await combat('опасный удар', '⚠️ Вы: 20/100 · /flee', urgent=True)
        urgent = await store.claim()
        assert 'опасный удар' in urgent['message_text'] and '/flee' in urgent['message_text']
        assert not urgent['combat_open']
        # Claimed/uncertain messages are immutable even if new hits arrive.
        assert await combat('следующий удар')
        await store.finish(urgent, 'pending', 'transport_error', 10)
        before_retry = (await rows())[0]['message_text']
        assert await combat('ещё удар', '❤️ Вы: 70/100')
        after = await rows()
        assert after[0]['message_text'] == before_retry and after[0]['attempts'] == 1
        assert after[1]['combat_log'] == 'следующий удар\nещё удар'
        await clear()
        # Oversize logs seal previous frame and split without text loss.
        await combat('старый удар')
        large = 'x' * 8000
        await combat(large)
        overflow = await rows()
        assert len(overflow) > 2 and all(len(r['message_text']) <= 3500 for r in overflow)
        assert ''.join(r['combat_log'] for r in overflow) == 'старый удар' + large
        assert not any(r['combat_open'] for r in overflow[:-1]) and overflow[-1]['combat_open']
        await clear()
        await combat('одна комната', key='room1')
        await combat('другая комната', key='room2')
        assert not (await rows())[0]['combat_open']
        assert len(await rows()) == 2
        await clear()
        # Failed overflow transaction restores the original open batch in full.
        await combat('исходный кадр')
        original = dict((await rows())[0])
        await db.pool.execute('''CREATE FUNCTION reject_combat_probe() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.combat_log LIKE 'ROLLBACK%' THEN RAISE EXCEPTION 'rollback probe'; END IF;
            RETURN NEW; END $$;
            CREATE TRIGGER combat_probe BEFORE INSERT ON max_outbox
            FOR EACH ROW EXECUTE FUNCTION reject_combat_probe();''')
        try:
            await combat('ROLLBACK' + large)
        except asyncpg.PostgresError:
            pass
        else:
            raise AssertionError('Expected combat enqueue rollback')
        assert [dict(r) for r in await rows()] == [original]
        await db.pool.execute('DROP TRIGGER combat_probe ON max_outbox')
        # Old summaries aren't delivered to a reset/recreated hero.
        old_gen = ch.generation
        await db.reset_character(uid, ch.generation)
        ch = Character(uid=uid, name='НовыйБоец', cls='warrior', race='human')
        ch.init_vitals()
        ch.init_skills()
        await db.create_character(ch)
        assert not await combat('устаревший', generation=old_gen)
        await db.pool.execute("UPDATE max_outbox SET next_attempt_at=now()")
        client = SimpleNamespace(send_chunk=AsyncMock())
        worker = MaxOutboxWorker(store, client)
        assert await worker.process_one()
        client.send_chunk.assert_not_awaited()
        assert (await rows())[0]['last_error'] == 'stale_combat'
        await clear()
        await combat('слишком старый')
        await db.pool.execute("UPDATE max_outbox SET expires_at=now()-interval '1 second',next_attempt_at=now()")
        await worker.process_one()
        client.send_chunk.assert_not_awaited()
        assert (await rows())[0]['last_error'] == 'expired'
    finally:
        await db.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


if __name__ == '__main__':
    dsn = os.getenv('TEST_DATABASE_URL')
    if dsn:
        asyncio.run(run(dsn))
        print('OK: PostgreSQL MAX combat batching, restart, concurrency, urgent boundaries, immutable retries, overflow, rollback and reset')
    else:
        print('SKIP: TEST_DATABASE_URL missing; PostgreSQL MAX combat tests run in CI')
