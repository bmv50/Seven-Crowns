"""Real database persistence and durable corpse buttons; no success on failed save."""
import asyncio
import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.max_transport import MaxInput
from engine import max_encounters, max_ui
from engine.db import Database
from engine.max_outbox import MaxOutboxStore, MaxOutboxWorker
from engine.world import World
from test_max_loot import actor, setup, loot_buttons, token


async def run(dsn):
    import asyncpg
    schema = 'test_max_loot_'+uuid.uuid4().hex
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
        ch = actor(uid)
        await db.create_character(ch)
        world = World(); mob = world.living_in(ch.room)[0]
        rare = 'железный_меч#purple#42'
        corpse = world.add_corpse(ch.room, mob, [rare, 'малое_зелье'])
        menu = max_ui.with_back(max_encounters.entity_rows(ch, world))
        payload = loot_buttons(ch, menu)[0]['payload']
        store = MaxOutboxStore(db.pool)
        await store.enqueue(uid, '42', 'Тело с добычей', keyboard=menu)
        await db.close(); await connect()
        store = MaxOutboxStore(db.pool)
        sender = SimpleNamespace(send_chunk=AsyncMock())
        worker = MaxOutboxWorker(store, sender)
        assert await worker.process_one()
        assert sender.send_chunk.await_args.kwargs['keyboard'] == menu
        ch = await db.load_active_character(uid)
        assert max_ui.resolve(ch, payload) == ('loot', max_encounters.corpse_key(corpse))
        replies = []
        async def send(target, message, **kwargs):
            if 'Вы обыскали' in message:
                saved = await db.load_active_character(target)
                assert saved.inventory == [rare, 'малое_зелье']
            replies.append(message)
            await store.enqueue(target, '42', message, keyboard=kwargs.get('max_keyboard'))
        handler, env, _ = setup(ch, world, db=db, send=send)
        await handler(MaxInput('42', 'loot:pg', payload))
        assert 'Вы обыскали' in replies[-1] and not world.corpses_in(ch.room)
        await handler(MaxInput('42', 'loot:repeat', payload))
        assert 'уже обыскали' in replies[-1]
        await db.close(); await connect()
        ch = await db.load_active_character(uid)
        assert ch.inventory == [rare, 'малое_зелье']
        # Actual PostgreSQL rollback: no false success or unsafe in-memory retry.
        store = MaxOutboxStore(db.pool)
        handler, env, _ = setup(ch, world, db=db, send=send)
        failed = world.add_corpse(ch.room, mob, ['малое_зелье'])
        await db.pool.execute('''CREATE FUNCTION reject_loot_save() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'loot save failure'; END; $$ LANGUAGE plpgsql;
            CREATE TRIGGER reject_loot_save BEFORE UPDATE ON characters
            FOR EACH ROW EXECUTE FUNCTION reject_loot_save();''')
        count = len(replies)
        try:
            await handler(MaxInput('42', 'loot:pg-failure', token(ch, failed)))
        except asyncpg.PostgresError:
            pass
        else:
            raise AssertionError('Failed persistence was reported as success')
        assert len(replies) == count
        env['_evict_stale'].assert_called_with(uid)
        assert (await db.load_active_character(uid)).inventory == [rare, 'малое_зелье']
        assert world.find_corpse(ch.room, failed['key']) is None
    finally:
        await db.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


if __name__ == '__main__':
    dsn = os.getenv('TEST_DATABASE_URL')
    if dsn:
        asyncio.run(run(dsn))
        print('OK: PostgreSQL MAX corpse buttons after worker restart, save-before-reply, seeded loot persistence and rollback')
    else:
        print('SKIP: TEST_DATABASE_URL missing; PostgreSQL MAX loot tests run in CI')
