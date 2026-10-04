"""Guild roster, bank, restart and reset against real isolated PostgreSQL."""
import asyncio
import os
import uuid
from unittest.mock import patch

from engine.db import Database
from engine.character import Character
from engine import guild_store, guild_tx


async def run(dsn):
    import asyncpg
    schema = 'test_guilds_' + uuid.uuid4().hex
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
        for uid in (1, -2, 3, 4):
            ch = Character(uid=uid, name=f'Герой{abs(uid)}', cls='warrior', race='human')
            ch.init_vitals()
            ch.init_skills()
            ch.level = 10
            ch.gold = 2000000
            ch.inventory = ['health_potion']
            await db.create_character(ch)
        async def action(uid, op, **kw):
            result, state = await guild_store.action(db.pool.acquire, uid, op, **kw)
            assert result[0], result
            return state
        state = await action(1, 'create', name='Стражи', op_id='create:1')
        gid = state[1][1]
        await action(1, 'invite', target=-2)
        await db.close()
        await connect()
        assert (await guild_store.load(db.pool.acquire))[2][-2] == gid
        await action(-2, 'accept')
        # A regular member may deposit, but may not withdraw or manage ranks.
        assert (await guild_tx.deposit_gold(db.pool.acquire, -2, gid, 100, 'dep'))[0]
        assert not (await guild_tx.withdraw_gold(db.pool.acquire, -2, gid, 10, 'denied'))[0]
        assert not (await guild_store.action(db.pool.acquire, -2, 'kick', target=1))[0][0]
        assert not (await guild_store.action(db.pool.acquire, -2, 'promote', target=-2))[0][0]
        for _ in range(2):
            assert (await guild_tx.deposit_gold(db.pool.acquire, 1, gid, 500, 'dep:repeat'))[0]
        state = await guild_store.load(db.pool.acquire)
        assert state[0][gid]['bank_gold'] == 600
        assert (await guild_tx.deposit_item(db.pool.acquire, -2, gid, 'health_potion', 'item:dep'))[0]
        assert (await guild_tx.withdraw_item(db.pool.acquire, 1, gid, 'health_potion', 'item:wd'))[0]
        assert (await guild_tx.withdraw_item(db.pool.acquire, 1, gid, 'health_potion', 'item:wd'))[0]
        async with db.pool.acquire() as conn:
            import json
            inv = await conn.fetchval('SELECT inventory FROM characters WHERE uid=1')
            assert json.loads(inv).count('health_potion') == 2
        # Concurrent invitations and membership updates cannot overwrite each other.
        await asyncio.gather(*(action(1, 'invite', target=u) for u in (3, 4)))
        await asyncio.gather(*(action(u, 'accept') for u in (3, 4)))
        state = await action(1, 'promote', target=-2)
        assert state[0][gid]['ranks']['-2'] == 'sergeant'
        before = await guild_store.load(db.pool.acquire)
        async def failing(mgr, conn):
            mgr.leave(-2)
            await conn.execute('DELETE FROM guild_members WHERE uid=$1', -2)
            raise RuntimeError('rollback probe')
        try:
            await guild_store.change(db.pool.acquire, failing)
        except RuntimeError:
            pass
        else:
            raise AssertionError('Expected rollback')
        assert await guild_store.load(db.pool.acquire) == before
        await action(1, 'leave')
        await db.close()
        await connect()
        state = await guild_store.load(db.pool.acquire)
        assert state[0][gid]['leader'] == -2
        assert state[0][gid]['ranks']['-2'] == 'leader'
        assert 1 not in state[1]
        # Reset removes membership in the same transaction and transfers leadership.
        await db.reset_character(-2, 1)
        state = await guild_store.load(db.pool.acquire)
        assert -2 not in state[1] and state[0][gid]['leader'] in (3, 4)
        await action(3, 'leave')
        assert not (await guild_store.action(db.pool.acquire, 4, 'leave'))[0][0]
        assert (await guild_tx.withdraw_gold(db.pool.acquire, 4, gid, 600, 'empty:bank'))[0]
        await action(4, 'leave')
        assert not (await guild_store.load(db.pool.acquire))[0]
        state = await action(1, 'create', name='Новые', op_id='create:next')
        assert state[1][1] != gid  # Dissolved ID must not be reused in the ledger.
        # Deleted hero must not create or accept a guild.
        try:
            await guild_store.action(db.pool.acquire, -2, 'create', name='Старые', op_id='deleted')
        except ValueError:
            pass
        else:
            raise AssertionError('Deleted hero accepted')
    finally:
        await db.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


if __name__ == '__main__':
    dsn = os.getenv('TEST_DATABASE_URL')
    if dsn:
        asyncio.run(run(dsn))
        print('OK: PostgreSQL guild restart, ranks, bank idempotency, concurrency, rollback and reset')
    else:
        print('SKIP: TEST_DATABASE_URL missing; guild PostgreSQL tests run in CI')
