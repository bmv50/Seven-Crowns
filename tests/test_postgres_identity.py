"""Real PostgreSQL migration and concurrent MAX identity reservation.

TEST_DATABASE_URL must point to an isolated test database, never production.
"""

import asyncio
import os
import sys
import uuid
from unittest.mock import patch

from engine.character import Character
from engine.db import Database, SCHEMA


async def run_integration(dsn):
    import asyncpg

    schema = "test_identity_" + uuid.uuid4().hex
    admin = await asyncpg.connect(dsn)
    db = Database(dsn)
    original_create_pool = asyncpg.create_pool

    def scoped_pool(*args, **kwargs):
        kwargs["server_settings"] = {"search_path": schema}
        return original_create_pool(*args, **kwargs)

    async def connect():
        with patch("engine.db.asyncpg.create_pool", side_effect=scoped_pool):
            await db.connect()

    created = False
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        created = True
        await admin.execute(f'SET search_path TO "{schema}"')
        legacy_schema = SCHEMA.split("-- Этап 4: внутренний uid", 1)[0]
        await admin.execute(legacy_schema)
        await admin.execute(
            "INSERT INTO characters (uid, name, cls) VALUES "
            "(123, 'СтарыйГерой', 'warrior'), (-1, 'СтарыйМинус', 'mage')")

        await connect()
        assert await db.resolve_player_id("telegram", 123) == 123
        assert await db.resolve_player_id("telegram", -1) is None
        assert await db.resolve_player_id("max", 123) is None

        ids = await asyncio.gather(*(db.reserve_max_player_id("same") for _ in range(8)))
        assert len(set(ids)) == 1 and ids[0] < -1
        other = await db.reserve_max_player_id("other")
        assert other < 0 and other != ids[0]
        assert await db.resolve_player_id("max", "same") == ids[0]
        assert await db.max_external_user_id(ids[0]) == "same"
        assert await db.max_external_user_id(123) is None
        assert await db.resolve_player_id("telegram", "same") is None

        assert await db.enqueue_max_update("message:same:mid-1", "same", "север")
        assert not await db.enqueue_max_update("message:same:mid-1", "same", "север")
        claimed = await db.claim_next_max_update()
        assert claimed["event_key"] == "message:same:mid-1"
        assert claimed["message_text"] == "север"
        assert await db.claim_next_max_update() is None
        await db.finish_max_update(claimed["event_key"])
        row = await admin.fetchrow(
            "SELECT status, message_text FROM max_inbox WHERE event_key=$1",
            claimed["event_key"])
        assert row["status"] == "done" and row["message_text"] is None

        ch = Character(uid=456, name="НовыйГерой", cls="warrior")
        ch.init_skills()
        ch.init_vitals()
        await db.create_character(ch)
        assert await db.resolve_player_id("telegram", 456) == 456
        assert await db.resolve_player_id("max", 456) is None

        await db.close()
        await connect()  # повтор миграции не меняет сопоставления
        assert await db.resolve_player_id("telegram", 123) == 123
        assert await db.resolve_player_id("max", "same") == ids[0]
        assert await db.reserve_max_player_id("same") == ids[0]
        rows = await admin.fetch("SELECT platform, external_user_id, uid FROM platform_identities")
        assert len(rows) == 4  # 123, 456, same, other; отрицательный legacy без alias
    finally:
        await db.close()
        if created:
            await admin.execute("SET search_path TO public")
            await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


def main():
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        print("SKIP: TEST_DATABASE_URL не задан; PostgreSQL identity не проверена")
        return 0
    asyncio.run(run_integration(dsn))
    print("OK: PostgreSQL identity migration, isolation, concurrent MAX IDs, restart")
    return 0


if __name__ == "__main__":
    sys.exit(main())
