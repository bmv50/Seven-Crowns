"""Atomic sale receipts, latest inventory/equipment checks and uncertain COMMIT."""
import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from unittest.mock import patch

from engine import content
from engine.character import Character
from engine.db import Database
from engine.shop_purchase import ShopPurchaseStore


async def run(dsn):
    import asyncpg
    schema = 'test_max_sale_' + uuid.uuid4().hex
    admin = await asyncpg.connect(dsn)
    db = Database(dsn)
    factory = asyncpg.create_pool
    def scoped(*args, **kwargs):
        kwargs['server_settings'] = {'search_path': schema}
        return factory(*args, **kwargs)
    async def connect():
        with patch('engine.db.asyncpg.create_pool', side_effect=scoped): await db.connect()
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        await connect()
        uid = await db.reserve_max_player_id('42')
        ch = Character(uid=uid, name='Продажа', race='human', cls='warrior')
        ch.init_vitals()
        ch.init_skills()
        ch.room, ch.gold = 'market', 100000
        key, vendor = 'малое_зелье', 'лавочник_туманного_брода'
        ch.inventory = [key]*3
        await db.create_character(ch)
        store = ShopPurchaseStore(db)
        # Simulate the preceding deployment's table without the operation column.
        ok, _, legacy = await store.quote(ch, vendor, key)
        assert ok
        await db.pool.execute('ALTER TABLE max_shop_intents DROP COLUMN operation')
        await db.close()
        await connect()
        assert await db.pool.fetchval('SELECT operation FROM max_shop_intents WHERE token=$1', legacy) == 'buy'
        assert (await store.confirm(ch, legacy, cancel=True))[0]
        async def quote(item=key, npc=vendor, op='sell'):
            ok, _, token = await store.quote(ch, npc, item, operation=op)
            assert ok
            return token
        async def confirm(token, cancel=False, op='sell'):
            return await store.confirm(ch, token, cancel=cancel, operation=op)
        price = content.sell_price(key)
        assert not (await store.quote(ch, 'жрец_храма', key, operation='sell'))[0]
        before = ch.gold
        token = await quote()
        assert ch.gold == before and ch.inventory.count(key) == 3
        assert not (await confirm(token, op='buy'))[0]
        results = await asyncio.gather(*(confirm(token) for _ in range(4)))
        assert all(result[0] for result in results)
        assert ch.gold == before+price and ch.inventory.count(key) == 2
        assert await db.pool.fetchval("SELECT count(*) FROM economy_ledger WHERE operation='shop_sell'") == 2
        assert await db.pool.fetchval('SELECT sum(gold_delta) FROM economy_ledger') == 0
        token = await quote()
        assert (await confirm(token, cancel=True))[0]
        assert not (await confirm(token))[0]
        token = await quote()
        await quote(op='buy')  # another trade cancels the old sale
        assert not (await confirm(token))[0]
        token = await quote()
        await db.pool.execute("UPDATE max_shop_intents SET expires_at=now()-interval '1 second' WHERE token=$1", token)
        assert not (await confirm(token))[0]
        token = await quote()
        ch.room = 'temple'
        assert not (await confirm(token))[0]
        ch.room = 'market'
        ch.target = 'mob'
        assert not (await confirm(token))[0]
        ch.target = None
        with patch('engine.content.sell_price', return_value=price+1):
            assert not (await confirm(token))[0]
        # A removed item and equipment changes after the quote are checked again.
        inventory = list(ch.inventory)
        ch.inventory = []
        assert not (await confirm(token))[0]
        ch.inventory = inventory
        sword, smith = 'ржавый_меч', 'оружейник_брода'
        ch.inventory.append(sword)
        token_sword = await quote(sword, smith)
        ch.equipment['weapon'] = sword
        assert not (await confirm(token_sword))[0]
        ch.inventory.append(sword)
        token_sword = await quote(sword, smith)
        assert (await confirm(token_sword))[0]
        assert ch.inventory.count(sword) == 1 and ch.equipment['weapon'] == sword
        other_uid = await db.reserve_max_player_id('43')
        other = Character(uid=other_uid, name='ДругойПродавец', race='human', cls='warrior')
        other.init_vitals()
        other.room, other.inventory = 'market', [key]
        await db.create_character(other)
        token = await quote()
        assert not (await store.confirm(other, token, operation='sell'))[0]
        # Receipt failure must roll back gold, removal and both ledger rows.
        await db.pool.execute('''CREATE FUNCTION fail_sale() RETURNS trigger AS $$ BEGIN
            IF NEW.status='done' THEN RAISE EXCEPTION 'injected sale failure'; END IF;
            RETURN NEW; END; $$ LANGUAGE plpgsql;
            CREATE TRIGGER fail_sale BEFORE UPDATE ON max_shop_intents
            FOR EACH ROW EXECUTE FUNCTION fail_sale();''')
        snapshot = (ch.gold, list(ch.inventory))
        ledger = await db.pool.fetchval('SELECT count(*) FROM economy_ledger')
        try: await confirm(token)
        except asyncpg.PostgresError: pass
        else: raise AssertionError('Injected sale failure ignored')
        assert snapshot == (ch.gold, ch.inventory)
        assert await db.pool.fetchval('SELECT count(*) FROM economy_ledger') == ledger
        assert await db.pool.fetchval('SELECT gold FROM characters WHERE uid=$1', uid) == snapshot[0]
        await db.pool.execute('DROP TRIGGER fail_sale ON max_shop_intents')
        # COMMIT success with lost acknowledgment leaves a cache-save fence.
        real_pool = db.pool
        class Tx:
            def __init__(self, tx): self.tx = tx
            async def __aenter__(self): return await self.tx.__aenter__()
            async def __aexit__(self, typ, value, tb):
                result = await self.tx.__aexit__(typ, value, tb)
                if typ is None: raise ConnectionError('lost sale COMMIT acknowledgment')
                return result
        class Con:
            def __init__(self, con): self.con = con
            def __getattr__(self, key): return getattr(self.con, key)
            def transaction(self): return Tx(self.con.transaction())
        class Pool:
            def __getattr__(self, key): return getattr(real_pool, key)
            @asynccontextmanager
            async def acquire(self):
                async with real_pool.acquire() as con: yield Con(con)
        with patch.object(db, 'pool', Pool()):
            try: await confirm(token)
            except ConnectionError: pass
            else: raise AssertionError('Lost sale COMMIT not simulated')
        assert uid in db._shop_uncertain and snapshot == (ch.gold, ch.inventory)
        ch.gold += 17
        ch.inventory.append('целебная_трава')
        await db.save(ch)
        assert ch.gold == snapshot[0]+price+17
        assert ch.inventory.count(key) == snapshot[1].count(key)-1
        assert 'целебная_трава' in ch.inventory and ch.equipment['weapon'] == sword
        balance, quantity = ch.gold, ch.inventory.count(key)
        assert (await confirm(token))[0]
        assert (ch.gold, ch.inventory.count(key)) == (balance, quantity)
        await db.close()
        await connect()
        ch = (await db.load_all())[uid]
        store = ShopPurchaseStore(db)
        assert (await confirm(token))[0]
        assert (ch.gold, ch.inventory.count(key)) == (balance, quantity)
        pending = await quote()
        await db.pool.execute('UPDATE characters SET generation=generation+1 WHERE uid=$1', uid)
        from engine.lifecycle_errors import StaleCharacterWrite
        try: await confirm(pending)
        except StaleCharacterWrite: pass
        else: raise AssertionError('Stale generation sale accepted')
    finally:
        await db.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


if __name__ == '__main__':
    dsn = os.getenv('TEST_DATABASE_URL')
    if dsn:
        asyncio.run(run(dsn))
        print('OK: PostgreSQL MAX sale atomicity, duplicates, owner/type/expiry/price/inventory/equipment guards, rollback, restart and COMMIT recovery')
    else:
        print('SKIP: TEST_DATABASE_URL missing; PostgreSQL sale tests run in CI')
