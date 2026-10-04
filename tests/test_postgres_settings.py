"""Preference commit, save serialization, rollback and generation guards."""
import asyncio
import json
import os
import uuid
from unittest.mock import patch

from engine.db import Database
from engine.character import Character
from engine.lifecycle_errors import StaleCharacterWrite


async def run(dsn):
    import asyncpg
    schema = 'test_settings_' + uuid.uuid4().hex
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
        ch = Character(uid=-10, name='Настройки', cls='warrior', race='human')
        ch.init_vitals()
        ch.init_skills()
        ch.flags = {'autoloot': False, 'quest_progress': 42,
                    'notify': {'world_boss': False}, 'notify_quota': {'count': 1}}
        await db.create_character(ch)
        await db.set_player_setting(ch, 'quiet', 'on')
        await db.set_player_setting(ch, 'tz', '+12')
        await db.set_player_setting(ch, 'limit', '5')
        await db.close()
        await connect()
        restored = (await db.load_all())[-10]
        assert restored.flags['notify'] == {'world_boss': False, 'quiet_off': False, 'tz_offset': 12, 'limit': 5}
        assert restored.flags['notify_quota'] == {'count': 1}
        # Existing row fields and money are not replaced by a preference update.
        assert restored.flags['quest_progress'] == 42 and restored.gold == ch.gold
        # A queued normal save must serialize AFTER committed preference publication.
        lock = db._character_write_locks.setdefault(ch.uid, asyncio.Lock())
        await lock.acquire()
        setter = asyncio.create_task(db.set_player_setting(ch, 'autoloot', 'on'))
        writer = asyncio.create_task(db.save(ch))
        await asyncio.sleep(0)
        lock.release()
        await asyncio.gather(setter, writer)
        assert (await db.load_all())[-10].flags['autoloot'] is True
        await db.set_player_setting(ch, 'autoloot', 'off')
        async with db.pool.acquire() as conn:
            await conn.execute("""CREATE FUNCTION reject_setting_probe() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN IF NEW.flags->>'autoloot' = 'true' THEN RAISE EXCEPTION 'rollback probe'; END IF;
                RETURN NEW; END $$;
                CREATE TRIGGER setting_probe BEFORE UPDATE OF flags ON characters
                FOR EACH ROW EXECUTE FUNCTION reject_setting_probe();""")
        try:
            await db.set_player_setting(ch, 'autoloot', 'on')
        except asyncpg.PostgresError:
            pass
        else:
            raise AssertionError('Expected transaction rollback')
        assert ch.flags['autoloot'] is False
        assert (await db.load_all())[-10].flags['autoloot'] is False
        await db.reset_character(ch.uid, ch.generation)
        try:
            await db.set_player_setting(ch, 'push', 'on')
        except StaleCharacterWrite:
            pass
        else:
            raise AssertionError('Deleted hero setting changed')
        new = Character(uid=-10, name='НовыйИгрок', cls='warrior', race='human')
        new.init_vitals()
        new.init_skills()
        await db.create_character(new)
        try:
            await db.set_player_setting(ch, 'push', 'on')
        except StaleCharacterWrite:
            pass
        else:
            raise AssertionError('Old generation setting changed')
        assert not (await db.load_all())[-10].flags.get('notify', {}).get('push_enabled', False)
    finally:
        await db.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


if __name__ == '__main__':
    dsn = os.getenv('TEST_DATABASE_URL')
    if dsn:
        asyncio.run(run(dsn))
        print('OK: PostgreSQL preferences restart, merge, serialized saves, rollback and generation guards')
    else:
        print('SKIP: TEST_DATABASE_URL missing; PostgreSQL preference tests run in CI')
