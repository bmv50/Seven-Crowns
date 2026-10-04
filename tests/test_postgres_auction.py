"""Real PostgreSQL auction concurrency, integer money and lifecycle rollback."""
import asyncio
import json
import os
import uuid
from unittest.mock import patch

from engine.db import Database
from engine.character import Character
from engine import econ_tx


async def run(dsn):
    import asyncpg
    schema = 'test_auction_' + uuid.uuid4().hex
    admin = await asyncpg.connect(dsn)
    db = Database(dsn)
    factory = asyncpg.create_pool
    def scoped(*args, **kwargs):
        kwargs['server_settings'] = {'search_path': schema, 'statement_timeout': '8000'}
        return factory(*args, **kwargs)
    async def connect():
        with patch('engine.db.asyncpg.create_pool', side_effect=scoped):
            await db.connect()
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        await connect()
        key = 'малое_зелье'
        for uid in (-10, 20, 30):
            ch = Character(uid=uid, name=f'Герой{abs(uid)}', cls='warrior', race='human')
            ch.init_vitals()
            ch.init_skills()
            ch.level, ch.gold, ch.inventory = 12, 1000000, [key] * 20
            await db.create_character(ch)
        cf = db.pool.acquire
        # Seller row lock enforces the cap even when 11 requests arrive together.
        results = await asyncio.gather(*(econ_tx.list_lot(cf, -10, key, 101, f'L{i}', f'list:{i}') for i in range(11)))
        assert sum(result[0] for result in results) == 10
        assert len(await econ_tx.load_active_lots(cf)) == 10
        lid = (await econ_tx.load_active_lots(cf))[0]['id']
        assert not (await econ_tx.buy_lot(cf, -10, lid, 'own'))[0]
        assert not (await econ_tx.cancel_lot(cf, 20, lid, 'other'))[0]
        assert not (await econ_tx.list_lot(cf, 20, key, -1, 'negative', 'negative'))[0]
        assert not (await econ_tx.list_lot(cf, 20, key, 0, 'zero', 'zero'))[0]
        # Two buyers, one winner, one item; proceeds 95, commission 6.
        results = await asyncio.gather(*(econ_tx.buy_lot(cf, uid, lid, f'buy:{uid}') for uid in (20, 30)))
        assert sum(result[0] for result in results) == 1
        winner = 20 if results[0][0] else 30
        assert (await econ_tx.buy_lot(cf, winner, lid, f'buy:{winner}'))[0]
        async with cf() as conn:
            assert await conn.fetchval('SELECT gold FROM characters WHERE uid=$1', winner) == 999899
            assert await conn.fetchval('SELECT gold FROM characters WHERE uid=-10') == 1000095
            inv = json.loads(await conn.fetchval('SELECT inventory FROM characters WHERE uid=$1', winner))
            assert inv.count(key) == 21
            assert await conn.fetchval("SELECT sum(gold_delta) FROM economy_ledger WHERE operation_id LIKE 'buy:%'") == 0
        # Failure on ledger insertion must undo balances, item transfer and sold status.
        fail_lid = next(l['id'] for l in await econ_tx.load_active_lots(cf))
        async with cf() as conn:
            before = await conn.fetch('SELECT uid,gold,inventory FROM characters ORDER BY uid')
            await conn.execute("""CREATE FUNCTION reject_auction_probe() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN IF NEW.operation_id = 'fail:buy' THEN RAISE EXCEPTION 'rollback probe'; END IF;
                RETURN NEW; END $$;
                CREATE TRIGGER auction_probe BEFORE INSERT ON economy_ledger
                FOR EACH ROW EXECUTE FUNCTION reject_auction_probe();""")
        assert not (await econ_tx.buy_lot(cf, 20, fail_lid, 'fail:buy'))[0]
        async with cf() as conn:
            assert await conn.fetch('SELECT uid,gold,inventory FROM characters ORDER BY uid') == before
            assert await conn.fetchval('SELECT status FROM auction_listings WHERE lot_id=$1', fail_lid) == 'active'
        # Purchase and cancellation race must not deadlock or duplicate escrow.
        results = await asyncio.wait_for(asyncio.gather(
            econ_tx.buy_lot(cf, 20, fail_lid, 'race:buy'),
            econ_tx.cancel_lot(cf, -10, fail_lid, 'race:cancel')), 10)
        assert sum(result[0] for result in results) == 1
        await db.close()
        await connect()
        cf = db.pool.acquire
        assert len(await econ_tx.load_active_lots(cf)) == 8
        # Soft reset returns remaining escrow before deleting the hero.
        await db.reset_character(-10, 1)
        assert not await econ_tx.load_active_lots(cf)
        restored = await db.restore_character(-10)
        assert restored.inventory.count(key) in (18, 19)  # one sale plus the buy/cancel race
        assert not (await econ_tx.cancel_lot(cf, -10, fail_lid, 'late:cancel'))[0]
        # Fresh hero cannot inherit an old listing after another reset.
        await db.reset_character(-10, restored.generation)
        new = Character(uid=-10, name='НовыйТорговец', cls='warrior', race='human')
        new.init_vitals()
        new.init_skills()
        await db.create_character(new)
        assert not await econ_tx.load_active_lots(cf)
        # Reset racing a sale has one consistent outcome and no deadlock.
        assert (await econ_tx.list_lot(cf, 30, key, 103, 'reset:race', 'reset:list'))[0]
        await asyncio.wait_for(asyncio.gather(db.reset_character(30, 1),
            econ_tx.buy_lot(cf, 20, 'reset:race', 'reset:buy')), 10)
        assert not await econ_tx.load_active_lots(cf)
    finally:
        await db.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


if __name__ == '__main__':
    dsn = os.getenv('TEST_DATABASE_URL')
    if dsn:
        asyncio.run(run(dsn))
        print('OK: PostgreSQL auction cap, competing buyers, rollback, restart, cancellation and reset races')
    else:
        print('SKIP: TEST_DATABASE_URL missing; PostgreSQL auction tests run in CI')
