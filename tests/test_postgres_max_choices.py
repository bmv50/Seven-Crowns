"""Story choices survive restarts, commit uncertainty and duplicate confirmations."""
import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from unittest.mock import patch

from engine import content, quest
from engine.character import Character
from engine.db import Database
from engine.max_choice import ChoiceStore


async def run(dsn):
    import asyncpg
    schema = 'test_max_choices_' + uuid.uuid4().hex
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
        ch = Character(uid=uid, name='Выбор', race='human', cls='warrior')
        ch.init_vitals()
        ch.room = 'temple'
        qid = 'sample_choose_faith'
        ch.quests[qid] = 'active'
        ch.flags['unrelated'] = {'keep': True}
        await db.create_character(ch)
        store = ChoiceStore(db)
        async def quote(option='light'):
            ok, message, token = await store.quote(ch, qid, option)
            assert ok, message
            return token
        old = await quote()
        token = await quote()
        assert quest.choice_made(ch, qid) is None
        assert not (await store.confirm(ch, old))[0]
        assert not (await store.confirm(ch, 'bad'))[0]
        assert (await store.confirm(ch, token, cancel=True))[0]
        assert not (await store.confirm(ch, token))[0]
        token = await quote()
        await db.pool.execute("UPDATE max_choice_intents SET expires_at=now()-interval '1 second' WHERE token=$1", token)
        assert not (await store.confirm(ch, token))[0]
        token = await quote()
        other_uid = await db.reserve_max_player_id('43')
        other = Character(uid=other_uid, name='Чужой', race='human', cls='warrior')
        other.init_vitals()
        other.room, other.quests[qid] = 'temple', 'active'
        await db.create_character(other)
        assert not (await store.confirm(other, token))[0]
        ch.room = 'market'
        assert not (await store.confirm(ch, token))[0]
        ch.room = 'temple'
        ch.target = 'mob'
        assert not (await store.confirm(ch, token))[0]
        ch.target = None
        ch.flags['dead'] = True
        assert not (await store.confirm(ch, token))[0]
        ch.flags.pop('dead')
        with patch('engine.quest.choose_option', return_value={'id': 'light', 'text': 'changed'}):
            assert not (await store.confirm(ch, token))[0]
        ch.quests[qid] = 'done'
        assert not (await store.confirm(ch, token))[0]
        ch.quests[qid] = 'active'
        # Persisted offer survives process restart before its confirmation.
        await db.save(ch)
        await db.close()
        await connect()
        ch = (await db.load_all())[uid]
        await db.pool.execute('''CREATE FUNCTION fail_choice() RETURNS trigger AS $$ BEGIN
            IF NEW.status='done' THEN RAISE EXCEPTION 'injected choice failure'; END IF;
            RETURN NEW; END; $$ LANGUAGE plpgsql;
            CREATE TRIGGER fail_choice BEFORE UPDATE ON max_choice_intents
            FOR EACH ROW EXECUTE FUNCTION fail_choice();''')
        try:
            await store.confirm(ch, token)
        except asyncpg.PostgresError:
            pass
        else:
            raise AssertionError('Rollback failure not injected')
        assert quest.choice_made(ch, qid) is None
        assert await db.pool.fetchval("SELECT flags->'quest_choices' FROM characters WHERE uid=$1", uid) is None
        await db.pool.execute('DROP TRIGGER fail_choice ON max_choice_intents')
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
                raise AssertionError('Lost ACK not simulated')
        assert uid in db._choice_uncertain and quest.choice_made(ch, qid) is None
        ch.flags['reward'] = True
        await db.save(ch)
        assert quest.choice_made(ch, qid) == 'light' and ch.flags['reward']
        assert ch.flags['unrelated'] == {'keep': True}
        results = await asyncio.gather(*(store.confirm(ch, token) for _ in range(4)))
        assert all(r[0] for r in results)
        assert not (await store.quote(ch, qid, 'dark'))[0]
        await db.close()
        await connect()
        ch = (await db.load_all())[uid]
        assert (await store.confirm(ch, token))[0] and quest.choice_made(ch, qid) == 'light'
        from engine.lifecycle_errors import StaleCharacterWrite
        await db.pool.execute('UPDATE characters SET generation=generation+1 WHERE uid=$1', uid)
        try:
            await store.confirm(ch, token)
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
        print('OK: PostgreSQL MAX story choices: restart, duplicate/cancel/expiry/owner/room/content/status/death guards, rollback and lost-COMMIT recovery')
    else:
        print('SKIP: TEST_DATABASE_URL missing; PostgreSQL story choice tests run in CI')
