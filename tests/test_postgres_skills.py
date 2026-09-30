# -*- coding: utf-8 -*-
"""Настоящий PostgreSQL: миграция и полный жизненный цикл умений персонажа.

По умолчанию пропускается. TEST_DATABASE_URL должен указывать на отдельную
тестовую БД, не на рабочую DATABASE_URL. Таблицы создаются в уникальной схеме,
которая удаляется в finally. CI запускает этот файл с PostgreSQL 16.
"""
import asyncio
import json
import os
import sys
import uuid
from unittest.mock import patch

from engine.character import Character
from engine.content import CLASSES
from engine.db import Database, SCHEMA
from engine.lifecycle_errors import StaleCharacterWrite
from engine import skills


async def run_integration(dsn):
    import asyncpg

    schema = "test_skills_" + uuid.uuid4().hex
    admin = await asyncpg.connect(dsn)
    db = Database(dsn)
    created = False
    original_create_pool = asyncpg.create_pool

    def scoped_pool(*args, **kwargs):
        kwargs["server_settings"] = {"search_path": schema}
        return original_create_pool(*args, **kwargs)

    async def connect():
        with patch("engine.db.asyncpg.create_pool", side_effect=scoped_pool):
            await db.connect()

    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        created = True
        await admin.execute(f'SET search_path TO "{schema}"')
        # Существующая таблица до исправления: новых колонок действительно нет.
        legacy_schema = SCHEMA.split("CREATE TABLE IF NOT EXISTS audit_log", 1)[0]
        legacy_schema = legacy_schema.replace("    learned    JSONB NOT NULL DEFAULT '[]',\n", "")
        legacy_schema = legacy_schema.replace("    loadout    JSONB NOT NULL DEFAULT '[]',\n", "")
        assert "learned" not in legacy_schema and "loadout" not in legacy_schema
        await admin.execute(legacy_schema)
        await admin.execute(
            "INSERT INTO characters (uid, name, cls, gold) VALUES ($1,$2,$3,$4)",
            1, "СтарыйГерой", "warrior", 12345)
        await connect()
        columns = await admin.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=$1 AND table_name='characters'", schema)
        assert {"learned", "loadout"} <= {r["column_name"] for r in columns}
        legacy = (await db.load_all())[1]
        assert legacy.learned == legacy.class_basics and legacy.gold == 12345

        expected = {}
        for uid, cls in enumerate(CLASSES, 100):
            ch = Character(uid=uid, name=f"Герой{uid}", cls=cls)
            ch.init_skills()
            ch.init_vitals()
            await db.create_character(ch)
            initial = (await db.load_all())[uid]
            assert initial.learned == ch.learned and initial.loadout == ch.loadout
            ch.level, ch.gold = 25, 1000000
            extra = next(s for s in skills.all_class_skills(cls) if not skills.is_basic(s))
            ok, why = skills.learn(ch, extra)
            assert ok, why
            ch.loadout = [extra, ch.class_basics[0]]
            skills.save_preset(ch, 1)
            await db.save(ch)
            expected[uid] = (list(ch.learned), list(ch.loadout), ch.gold, extra)

        # Новый пул + повторная миграция: проверяет рестарт и идемпотентность ALTER.
        await db.close()
        await connect()
        loaded = await db.load_all()
        for uid, (learned, loadout, gold, extra) in expected.items():
            ch = loaded[uid]
            assert ch.learned == learned and ch.loadout == loadout and ch.gold == gold
            ok, _ = skills.learn(ch, extra)
            assert not ok and ch.gold == gold
            ch.loadout = list(ch.class_basics)
            assert skills.load_preset(ch, 1) and ch.loadout == loadout

        old = loaded[100]
        await db.reset_character(old.uid, old.generation)
        deleted = await db.find_deleted(old.uid)
        assert deleted.learned == old.learned and deleted.loadout == old.loadout
        restored = await db.restore_character(old.uid)
        assert restored.learned == old.learned and restored.loadout == old.loadout
        old.learned, old.loadout = [], []
        try:
            await db.save(old)
        except StaleCharacterWrite:
            pass
        else:
            raise AssertionError("Stale save must not erase restored skills")
        unchanged = (await db.load_all())[restored.uid]
        assert unchanged.learned == restored.learned and unchanged.loadout == restored.loadout

        await db.reset_character(restored.uid, restored.generation)
        new = Character(uid=restored.uid, name="НовыйМаг", cls="mage")
        new.init_skills()
        await db.create_character(new)
        recreated = (await db.load_all())[new.uid]
        assert recreated.learned == new.learned and recreated.loadout == new.loadout
        assert not recreated.flags.get("presets")

        # Явно пустая панель и сброс реморта сохраняются, старые умения не всплывают.
        hero = loaded[101]
        hero.loadout = []
        await db.save(hero)
        assert (await db.load_all())[hero.uid].loadout == []
        assert hero.remort()
        await db.save(hero)
        reborn = (await db.load_all())[hero.uid]
        assert reborn.level == 1 and reborn.learned == hero.class_basics
        assert reborn.loadout == hero.loadout
        raw = await admin.fetchrow("SELECT learned, loadout FROM characters WHERE uid=$1", hero.uid)
        assert json.loads(raw["learned"]) == hero.learned
        assert json.loads(raw["loadout"]) == hero.loadout
    finally:
        try:
            await db.close()
        finally:
            try:
                # Только схема, созданная этим тестом; данные других схем не трогаем.
                if created:
                    await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
            finally:
                await admin.close()


def main():
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        print("SKIP: TEST_DATABASE_URL не задан; PostgreSQL-интеграция не проверена")
        return 0
    asyncio.run(run_integration(dsn))
    print("OK: PostgreSQL — миграция, 6 классов, рестарт, restore, recreate, stale-save и реморт")
    return 0


if __name__ == "__main__":
    sys.exit(main())
