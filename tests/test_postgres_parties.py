"""Party restart, concurrent changes and rollback in isolated PostgreSQL."""

import asyncio
import os
import uuid
from unittest.mock import patch

from engine.db import Database
from engine.character import Character
from engine import party_store
from engine.social import PartyManager


async def run_integration(dsn):
    import asyncpg
    schema = "test_parties_" + uuid.uuid4().hex
    admin = await asyncpg.connect(dsn)
    db = Database(dsn)
    create_pool = asyncpg.create_pool
    def scoped_pool(*args, **kwargs):
        kwargs["server_settings"] = {"search_path": schema}
        return create_pool(*args, **kwargs)
    async def connect():
        with patch("engine.db.asyncpg.create_pool", side_effect=scoped_pool):
            await db.connect()
    created = False
    valid = {10, -20, 30, 40, 50}
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        created = True
        await connect()
        for uid in valid:
            ch = Character(uid=uid, name=f"Герой{abs(uid)}", cls="warrior", race="human")
            ch.init_vitals()
            ch.init_skills()
            await db.create_character(ch)
        result, _ = await party_store.change(db.pool.acquire, lambda mgr: mgr.invite(10, -20), valid)
        assert result
        await db.close()
        await connect()
        restored = PartyManager()
        restored.import_state(await party_store.load(db.pool.acquire, valid))
        assert restored.invites[-20] == 10
        result, state = await party_store.change(db.pool.acquire, lambda mgr: mgr.accept(-20), valid)
        assert result["members"] == [10, -20]
        # Concurrent row-locked writes must preserve both invitations.
        results = await asyncio.gather(*(
            party_store.change(db.pool.acquire, lambda mgr, uid=uid: mgr.invite(10, uid), valid)
            for uid in (30, 40)))
        assert all(result[0] for result in results)
        restored.import_state(await party_store.load(db.pool.acquire, valid))
        assert restored.invites == {30: 10, 40: 10}
        before = restored.export_state()
        def failed_operation(mgr):
            mgr.leave(10)
            raise RuntimeError("rollback probe")
        try:
            await party_store.change(db.pool.acquire, failed_operation, valid)
        except RuntimeError:
            pass
        else:
            raise AssertionError("transaction should fail")
        assert await party_store.load(db.pool.acquire, valid) == before
        await db.close()
        await connect()
        restored.import_state(await party_store.load(db.pool.acquire, valid))
        assert restored.members(-20) == [10, -20]
        # Deleted leader is removed; leadership transfers and old invites expire.
        pruned = await party_store.load(db.pool.acquire, valid - {10})
        restored.import_state(pruned)
        assert restored.party_of(-20)["leader"] == -20 and not restored.invites
        assert await party_store.load(db.pool.acquire, valid - {10}) == pruned
        # Reset removes membership atomically; a new hero with the same uid
        # must not inherit the former hero's group or invitations.
        await db.reset_character(-20, 1)
        try:
            await party_store.change(db.pool.acquire, lambda mgr: mgr.invite(30, -20),
                                     valid, required_uids=(30, -20))
        except ValueError:
            pass
        else:
            raise AssertionError("Deleted character must not receive invitations")
        recreated = Character(uid=-20, name="НовыйНапарник", cls="warrior", race="human")
        recreated.init_vitals()
        recreated.init_skills()
        await db.create_character(recreated)
        restored.import_state(await party_store.load(db.pool.acquire, valid))
        assert restored.party_of(-20) is None
    finally:
        await db.close()
        if created:
            await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


if __name__ == "__main__":
    dsn = os.getenv("TEST_DATABASE_URL")
    if dsn:
        asyncio.run(run_integration(dsn))
        print("OK: PostgreSQL party restart, concurrent invites, rollback and deleted players")
    else:
        print("SKIP: TEST_DATABASE_URL не задан; PostgreSQL-группы не проверены")
