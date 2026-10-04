"""Real PostgreSQL purchase atomicity, duplicate confirmations and COMMIT recovery."""
import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from unittest.mock import patch

from engine import game_actions
from engine.character import Character, START_ROOM
from engine.db import Database
from engine.shop_purchase import ShopPurchaseStore


async def run(dsn):
    import asyncpg
    schema = 'test_max_shop_' + uuid.uuid4().hex
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
        ch = Character(uid=uid, name='Магазин', race='human', cls='warrior')
        ch.init_vitals()
        ch.init_skills()
        ch.room, ch.gold = 'market', 100000
        await db.create_character(ch)
        store = ShopPurchaseStore(db)
        vendor = game_actions.vendors_here(ch)[0]
        key = 'малое_зелье'
        price = game_actions.shop_price(ch, key, vendor)
        async def quote():
            ok, _, token = await store.quote(ch, vendor, key)
            assert ok
            return token
        before = ch.gold
        token = await quote()
        assert ch.gold == before and key not in ch.inventory
        assert await db.pool.fetchval('SELECT gold FROM characters WHERE uid=$1', uid) == before
        # New message IDs and concurrent clicks still debit a single intent once.
        results = await asyncio.gather(*(store.confirm(ch, token) for _ in range(4)))
        assert all(result[0] for result in results)
        assert ch.gold == before-price and ch.inventory.count(key) == 1
        assert await db.pool.fetchval('SELECT count(*) FROM economy_ledger WHERE operation=$1', 'shop_buy') == 2
        assert await db.pool.fetchval('SELECT sum(gold_delta) FROM economy_ledger') == 0
        token = await quote()
        assert (await store.confirm(ch, token, cancel=True))[0]
        assert not (await store.confirm(ch, token))[0]
        assert ch.inventory.count(key) == 1
        older = await quote()
        token = await quote()
        assert not (await store.confirm(ch, older))[0]
        await db.pool.execute("UPDATE max_shop_intents SET expires_at=now()-interval '1 second' WHERE token=$1", token)
        assert not (await store.confirm(ch, token))[0]
        token = await quote()
        ch.room = 'temple'
        assert not (await store.confirm(ch, token))[0]
        ch.room = 'market'
        token = await quote()
        ch.target = 'mob'
        assert not (await store.confirm(ch, token))[0]
        ch.target = None
        with patch('engine.game_actions.shop_price', return_value=price+1):
            assert not (await store.confirm(ch, token))[0]
        ch.gold = 0
        assert not (await store.confirm(ch, token))[0]
        ch.gold = before-price
        other_uid = await db.reserve_max_player_id('43')
        other = Character(uid=other_uid, name='Чужой', race='human', cls='warrior')
        other.init_vitals()
        other.room, other.gold = 'market', 100000
        await db.create_character(other)
        assert not (await store.confirm(other, token))[0]
        assert other.inventory == []
        # Failure after debit SQL but before receipt rolls back debit, item and journal.
        await db.pool.execute('''CREATE FUNCTION fail_shop() RETURNS trigger AS $$ BEGIN
            IF NEW.status='done' THEN RAISE EXCEPTION 'injected receipt failure'; END IF;
            RETURN NEW; END; $$ LANGUAGE plpgsql;
            CREATE TRIGGER fail_shop BEFORE UPDATE ON max_shop_intents
            FOR EACH ROW EXECUTE FUNCTION fail_shop();''')
        snapshot = (ch.gold, list(ch.inventory))
        try:
            await store.confirm(ch, token)
        except asyncpg.PostgresError:
            pass
        else:
            raise AssertionError('Rollback injection ignored')
        assert snapshot == (ch.gold, ch.inventory)
        assert await db.pool.fetchval('SELECT gold FROM characters WHERE uid=$1', uid) == snapshot[0]
        assert await db.pool.fetchval('SELECT count(*) FROM economy_ledger') == 2
        await db.pool.execute('DROP TRIGGER fail_shop ON max_shop_intents')

        # Simulate COMMIT succeeding but its ACK being lost. Subsequent saves must
        # reconcile the debit before they can overwrite the durable character row.
        real_pool = db.pool
        class Tx:
            def __init__(self, tx): self.tx = tx
            async def __aenter__(self): return await self.tx.__aenter__()
            async def __aexit__(self, typ, value, tb):
                result = await self.tx.__aexit__(typ, value, tb)
                if typ is None: raise ConnectionError('lost COMMIT acknowledgment')
                return result
        class Con:
            def __init__(self, con): self.con = con
            def __getattr__(self, key): return getattr(self.con, key)
            def transaction(self): return Tx(self.con.transaction())
        class Pool:
            def __getattr__(self, key): return getattr(real_pool, key)
            @asynccontextmanager
            async def acquire(self):
                async with real_pool.acquire() as con:
                    yield Con(con)
        with patch.object(db, 'pool', Pool()):
            try:
                await store.confirm(ch, token)
            except ConnectionError:
                pass
            else:
                raise AssertionError('Lost COMMIT ACK not simulated')
        assert uid in db._shop_uncertain and (ch.gold, ch.inventory) == snapshot
        ch.gold += 17  # an unrelated in-memory reward must be preserved on recovery
        await db.save(ch)
        assert uid not in db._shop_uncertain
        assert ch.gold == snapshot[0]-price+17 and ch.inventory.count(key) == 2
        assert (await store.confirm(ch, token))[0]
        assert ch.inventory.count(key) == 2
        # Receipt persists across restart and does not apply its debit again.
        await db.close()
        await connect()
        ch = (await db.load_all())[uid]
        store = ShopPurchaseStore(db)
        assert (await store.confirm(ch, token))[0]
        assert ch.inventory.count(key) == 2
        pending = await quote()
        await db.pool.execute('UPDATE characters SET generation=generation+1 WHERE uid=$1', uid)
        from engine.lifecycle_errors import StaleCharacterWrite
        try:
            await store.confirm(ch, pending)
        except StaleCharacterWrite:
            pass
        else:
            raise AssertionError('Stale generation purchase accepted')
    finally:
        await db.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


if __name__ == '__main__':
    dsn = os.getenv('TEST_DATABASE_URL')
    if dsn:
        asyncio.run(run(dsn))
        print('OK: PostgreSQL MAX shop duplicate/cancel/expiry/owner/room/price/funds/combat guards, rollback and lost-COMMIT recovery')
    else:
        print('SKIP: TEST_DATABASE_URL missing; PostgreSQL shop tests run in CI')
