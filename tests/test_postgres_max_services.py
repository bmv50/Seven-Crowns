"""Real PostgreSQL service transactions, failure injection and restart receipts."""
import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from unittest.mock import patch

from engine import skills
from engine.character import Character
from engine.db import Database
from engine.lifecycle_errors import StaleCharacterWrite
from engine.service_purchase import ServicePurchaseStore


async def run(dsn):
    import asyncpg
    schema = 'test_max_services_' + uuid.uuid4().hex
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
        ch = Character(uid=uid, name='Услуги', race='human', cls='warrior')
        ch.init_vitals()
        ch.init_skills()
        ch.room, ch.gold, ch.level = 'mine_entrance', 100000, 30
        ch.equipment['weapon'] = 'ржавый_меч'
        ch.set_durab('weapon', 50)
        ch.flags['unrelated'] = {'keep': True}
        await db.create_character(ch)
        store = ServicePurchaseStore(db)
        async def quote(operation='repair', subject=''):
            ok, message, token = await store.quote(ch, operation, subject)
            assert ok, message
            return token
        price = ch.repair_cost()
        before = ch.gold
        token = await quote()
        assert ch.gold == before and ch.durab('weapon') == 50
        assert not (await store.confirm(ch, token, 'learn'))[0]
        other_uid = await db.reserve_max_player_id('43')
        other = Character(uid=other_uid, name='Чужой', race='human', cls='warrior')
        other.init_vitals()
        await db.create_character(other)
        assert not (await store.confirm(other, token, 'repair'))[0]
        results = await asyncio.gather(*(store.confirm(ch, token, 'repair') for _ in range(4)))
        assert all(r[0] for r in results)
        assert ch.gold == before-price and ch.durab('weapon') == 100
        assert ch.flags['unrelated'] == {'keep': True}
        assert await db.pool.fetchval('SELECT count(*) FROM economy_ledger') == 2
        assert await db.pool.fetchval('SELECT sum(gold_delta) FROM economy_ledger') == 0
        assert not (await store.quote(ch, 'repair'))[0]
        ch.set_durab('weapon', 50)
        token = await quote()
        ch.set_durab('weapon', 49)
        assert not (await store.confirm(ch, token, 'repair'))[0]
        ch.set_durab('weapon', 50)
        ch.equipment['weapon'] = 'железный_меч'
        assert not (await store.confirm(ch, token, 'repair'))[0]
        ch.equipment['weapon'] = 'ржавый_меч'
        ch.room = 'market'
        assert not (await store.confirm(ch, token, 'repair'))[0]
        ch.room = 'mine_entrance'
        ch.target = 'mob'
        assert not (await store.confirm(ch, token, 'repair'))[0]
        ch.target = None
        ch.gold = 0
        assert not (await store.confirm(ch, token, 'repair'))[0]
        ch.gold = before-price
        with patch('engine.character.REPAIR_RATE', 999):
            assert not (await store.confirm(ch, token, 'repair'))[0]
        assert (await store.confirm(ch, token, 'repair', cancel=True))[0]
        assert not (await store.confirm(ch, token, 'repair'))[0]
        old = await quote()
        token = await quote()
        assert not (await store.confirm(ch, old, 'repair'))[0]
        await db.pool.execute("UPDATE max_service_intents SET expires_at=now()-interval '1 second' WHERE token=$1", token)
        assert not (await store.confirm(ch, token, 'repair'))[0]
        ch.room = 'trainers_hall'
        sid = 'whirlwind'
        price = skills.learn_cost(sid)
        before = ch.gold
        token = await quote('learn', sid)
        assert sid not in ch.learned and ch.gold == before
        ch.level = 1
        assert not (await store.confirm(ch, token, 'learn'))[0]
        ch.level = 30
        ch.cls = 'mage'
        assert not (await store.confirm(ch, token, 'learn'))[0]
        ch.cls = 'warrior'
        # Failure at receipt update rolls back gold, skill, panel and ledger.
        await db.pool.execute('''CREATE FUNCTION fail_service() RETURNS trigger AS $$ BEGIN
            IF NEW.status='done' THEN RAISE EXCEPTION 'injected service failure'; END IF;
            RETURN NEW; END; $$ LANGUAGE plpgsql;
            CREATE TRIGGER fail_service BEFORE UPDATE ON max_service_intents
            FOR EACH ROW EXECUTE FUNCTION fail_service();''')
        snapshot = (ch.gold, list(ch.learned), list(ch.loadout))
        try:
            await store.confirm(ch, token, 'learn')
        except asyncpg.PostgresError:
            pass
        else:
            raise AssertionError('Rollback failure not injected')
        assert snapshot == (ch.gold, ch.learned, ch.loadout)
        assert await db.pool.fetchval('SELECT gold FROM characters WHERE uid=$1', uid) == before
        assert await db.pool.fetchval('SELECT count(*) FROM economy_ledger') == 2
        await db.pool.execute('DROP TRIGGER fail_service ON max_service_intents')
        # Real commit, lost ACK: next snapshot must reconcile before persisting.
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
                await store.confirm(ch, token, 'learn')
            except ConnectionError:
                pass
            else:
                raise AssertionError('Lost COMMIT ACK not simulated')
        assert uid in db._service_uncertain and snapshot == (ch.gold, ch.learned, ch.loadout)
        ch.gold += 17
        await db.save(ch)
        assert uid not in db._service_uncertain and ch.gold == before-price+17
        assert ch.learned.count(sid) == 1 and ch.loadout.count(sid) == 1
        assert (await store.confirm(ch, token, 'learn'))[0]
        assert ch.gold == before-price+17
        # Free skills remain valid without bypassing durable confirmation.
        basic = 'power_strike'
        ch.learned.remove(basic)
        ch.loadout.remove(basic)
        free = await quote('learn', basic)
        balance = ch.gold
        assert (await store.confirm(ch, free, 'learn'))[0]
        assert ch.gold == balance and ch.learned.count(basic) == 1
        # Restart reuses the durable receipt, never buys the skill again.
        await db.close()
        await connect()
        ch = (await db.load_all())[uid]
        assert (await store.confirm(ch, token, 'learn'))[0]
        assert ch.gold == balance and ch.learned.count(sid) == 1
        assert await db.pool.fetchval('SELECT sum(gold_delta) FROM economy_ledger') == 0
        await db.pool.execute('UPDATE characters SET generation=generation+1 WHERE uid=$1', uid)
        try:
            await store.confirm(ch, token, 'learn')
        except StaleCharacterWrite:
            pass
        else:
            raise AssertionError('Stale generation accepted')
    finally:
        await db.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


if __name__ == '__main__':
    dsn = os.getenv('TEST_DATABASE_URL')
    if dsn:
        asyncio.run(run(dsn))
        print('OK: PostgreSQL MAX repair/learning duplicate, price/equipment/room/class/level/funds/expiry/owner guards, rollback, free skill, lost-COMMIT recovery and restart')
    else:
        print('SKIP: TEST_DATABASE_URL missing; PostgreSQL service tests run in CI')
