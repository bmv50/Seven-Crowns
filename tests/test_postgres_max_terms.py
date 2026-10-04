"""Versioned MAX consent persists independently of gameplay flags and hero lifecycle."""
import asyncio
import os
import uuid
from unittest.mock import patch

from engine.db import Database
from engine.max_terms import ConsentStore


async def run(dsn):
    import asyncpg
    schema = 'test_max_terms_' + uuid.uuid4().hex
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
        store = ConsentStore(db)
        assert not await store.accepted(uid, 'v1')
        await asyncio.gather(*(store.accept(uid, 'v1') for _ in range(4)))
        assert await store.accepted(uid, 'v1')
        stamp = await db.pool.fetchval('SELECT accepted_at FROM max_terms_acceptance WHERE uid=$1', uid)
        await store.accept(uid, 'v1')
        assert await db.pool.fetchval('SELECT accepted_at FROM max_terms_acceptance WHERE uid=$1', uid) == stamp
        assert not await store.accepted(uid, 'v2')
        await db.close()
        await connect()
        assert await store.accepted(uid, 'v1')
        await store.revoke(uid)
        assert not await store.accepted(uid, 'v1')
        await store.accept(uid, 'v2')
        assert await store.accepted(uid, 'v2') and not await store.accepted(uid, 'v1')
        # No character snapshot or notification flag is created by accepting.
        assert await db.pool.fetchval('SELECT count(*) FROM characters WHERE uid=$1', uid) == 0
        from scripts.max_preflight import check_database
        connect_factory = asyncpg.connect
        async def scoped_connect(*args, **kwargs):
            kwargs['server_settings'] = {'search_path': schema}
            return await connect_factory(*args, **kwargs)
        with patch.object(asyncpg, 'connect', side_effect=scoped_connect):
            report = await check_database(dsn)
        assert report['connected'] and report['schema_ready']
        assert report['inbox_counts'] == report['outbox_counts'] == {}
        assert report['stalled_outbox'] == 0
        assert await db.pool.fetchval('SELECT count(*) FROM characters') == 0
        try:
            await store.accept(42, 'v1')
        except ValueError:
            pass
        else:
            raise AssertionError('Telegram identity accepted as MAX consent')
    finally:
        await db.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


if __name__ == '__main__':
    dsn = os.getenv('TEST_DATABASE_URL')
    if dsn:
        asyncio.run(run(dsn))
        print('OK: PostgreSQL MAX terms consent duplicate timestamp, restart, revocation/version and character/push isolation')
    else:
        print('SKIP: TEST_DATABASE_URL missing; PostgreSQL consent tests run in CI')
