# -*- coding: utf-8 -*-
"""
Слой хранения на PostgreSQL (asyncpg).
Персонажи сериализуются: простые поля — в колонки, сложные — в JSONB.
Мобы/респавн — это рантайм-состояние мира, в БД не пишем (живёт в памяти).
"""
import json
import asyncio
import os
import time
from typing import Dict, List, Optional

try:
    import asyncpg
    # Аудит-2б.1: конкретный класс нарушения уникальности (name_norm-индекс).
    _UniqueViolationError = asyncpg.exceptions.UniqueViolationError
except ImportError:
    asyncpg = None   # БД опциональна: без asyncpg игра идёт без сохранения

    class _UniqueViolationError(Exception):
        """Заглушка без asyncpg — реальный конфликт возможен только с пулом."""

from .character import Character
from . import textsafe            # Аудит-2б.1: name_norm для уникальности имён
from .lifecycle_errors import (
    ActiveCharacterExists, StaleCharacterWrite, CharacterNotFound, RestoreExpired,
    NameTaken)

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/mud")


def _rowcount(status: str) -> int:
    """Число затронутых строк из тега команды asyncpg ('UPDATE 3' → 3, 'INSERT 0 1'
    → 1). Неизвестный формат → 0 (консервативно: считаем, что не записали)."""
    if not status:
        return 0
    parts = str(status).split()
    try:
        return int(parts[-1])
    except (ValueError, IndexError):
        return 0

SCHEMA = """
CREATE TABLE IF NOT EXISTS characters (
    uid        BIGINT PRIMARY KEY,
    name       TEXT NOT NULL,
    cls        TEXT NOT NULL,
    race       TEXT NOT NULL DEFAULT 'human',
    room       TEXT NOT NULL DEFAULT 'village',
    level      INT  NOT NULL DEFAULT 1,
    xp         INT  NOT NULL DEFAULT 0,
    hp         INT  NOT NULL DEFAULT 0,
    mp         INT  NOT NULL DEFAULT 0,
    gold       BIGINT NOT NULL DEFAULT 3000,
    equipment  JSONB NOT NULL DEFAULT '{}',
    inventory  JSONB NOT NULL DEFAULT '[]',
    quests     JSONB NOT NULL DEFAULT '{}',
    flags      JSONB NOT NULL DEFAULT '{}',
    learned    JSONB NOT NULL DEFAULT '[]',
    loadout    JSONB NOT NULL DEFAULT '[]',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen  TIMESTAMPTZ,
    notify_blocked BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_at TIMESTAMPTZ,
    -- Аудит-2а.1: поколение записи. Растёт при /reset и при пересоздании поверх
    -- удалённого. save() пишет строго строку своего поколения → stale-объект в
    -- памяти не может затереть нового/восстановленного героя.
    generation BIGINT NOT NULL DEFAULT 1,
    -- Аудит-2б.1: нормализованный ключ имени (NFKC→casefold→схлоп пробелов,
    -- см. engine/textsafe.name_norm). По нему строится ЧАСТИЧНЫЙ уникальный
    -- индекс активных персонажей (idx_characters_name_norm WHERE deleted_at IS
    -- NULL) — защита от имперсонации именами-двойниками. Индекс создаётся в
    -- connect() после backfill и проверки на дубли (не в SCHEMA — иначе на
    -- «грязной» БД падал бы весь SCHEMA).
    name_norm  TEXT
);
-- Этап 4: внутренний uid героя отделён от идентификатора мессенджера.
-- Старые положительные uid принадлежат Telegram; новые MAX uid выделяются
-- из отрицательного диапазона. Привязка двух каналов требует отдельного
-- подтверждения и не создаётся автоматически по имени или номеру телефона.
CREATE TABLE IF NOT EXISTS platform_identities (
    platform         TEXT NOT NULL CHECK (platform IN ('telegram', 'max')),
    external_user_id TEXT NOT NULL,
    uid              BIGINT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (platform, external_user_id),
    UNIQUE (uid, platform)
);
CREATE INDEX IF NOT EXISTS idx_platform_identities_uid ON platform_identities(uid);
CREATE SEQUENCE IF NOT EXISTS max_player_uid_seq AS BIGINT START WITH -1 INCREMENT BY -1;
-- Durable inbox: ACK after insert; one claim prevents replay of game mutations.
CREATE TABLE IF NOT EXISTS max_inbox (
    event_key TEXT PRIMARY KEY,
    external_user_id TEXT NOT NULL,
    message_text TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'done', 'failed')),
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_max_inbox_pending ON max_inbox(received_at)
    WHERE status='pending';
-- Delivery is retried independently; game inputs are NEVER replayed here.
CREATE TABLE IF NOT EXISTS max_outbox (
    id BIGSERIAL PRIMARY KEY,
    uid BIGINT NOT NULL CHECK (uid<0),
    external_user_id TEXT NOT NULL,
    message_text TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','processing','sent','failed')),
    attempts INT NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_until TIMESTAMPTZ,
    lease_token TEXT,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT now()+interval '24 hours',
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_max_outbox_active ON max_outbox(external_user_id,id)
    WHERE status IN ('pending','processing');
CREATE INDEX IF NOT EXISTS idx_max_outbox_finished ON max_outbox(finished_at)
    WHERE finished_at IS NOT NULL;
ALTER TABLE max_outbox ADD COLUMN IF NOT EXISTS category TEXT;
ALTER TABLE max_outbox ADD COLUMN IF NOT EXISTS generation BIGINT;
ALTER TABLE max_outbox ADD COLUMN IF NOT EXISTS dedup_key TEXT;
ALTER TABLE max_outbox ADD COLUMN IF NOT EXISTS combat_key TEXT;
ALTER TABLE max_outbox ADD COLUMN IF NOT EXISTS combat_open BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE max_outbox ADD COLUMN IF NOT EXISTS combat_log TEXT;
ALTER TABLE max_outbox ADD COLUMN IF NOT EXISTS combat_snapshot TEXT;
ALTER TABLE max_outbox ADD COLUMN IF NOT EXISTS keyboard JSONB;
CREATE UNIQUE INDEX IF NOT EXISTS idx_max_outbox_dedup ON max_outbox(dedup_key)
    WHERE dedup_key IS NOT NULL;
-- Журнал аудита необратимых действий игрока (/reset и восстановление персонажа).
-- Пишется при подтверждённом сбросе: чтобы разобрать спорную «пропажу» персонажа
-- и иметь след для поддержки на закрытой бете. Без пула (pool=None) — no-op.
CREATE TABLE IF NOT EXISTS audit_log (
    ts      DOUBLE PRECISION NOT NULL,
    uid     BIGINT NOT NULL,
    action  TEXT   NOT NULL,
    details JSONB
);
CREATE INDEX IF NOT EXISTS idx_audit_uid ON audit_log(uid);
CREATE TABLE IF NOT EXISTS notify_schedule (
    uid      BIGINT NOT NULL,
    category TEXT   NOT NULL,
    fire_at  DOUBLE PRECISION NOT NULL,
    payload  TEXT,
    PRIMARY KEY (uid, category)
);
CREATE INDEX IF NOT EXISTS idx_notify_fire ON notify_schedule(fire_at);
CREATE TABLE IF NOT EXISTS notify_log (
    uid      BIGINT NOT NULL,
    category TEXT   NOT NULL,
    ts       DOUBLE PRECISION NOT NULL,
    ok       BOOLEAN NOT NULL
);
-- Универсальное key-value хранилище для рантайм-состояния мира.
-- Один процесс, объёмы малы → простота важнее нормализации: снапшот мира,
-- таймеры боссов, аукцион и территории лежат отдельными строками (k='world',
-- 'boss_last', 'auction', 'territory', 'parties'), значение — цельный JSON.
CREATE TABLE IF NOT EXISTS kv_state (
    k       TEXT PRIMARY KEY,
    v       JSONB NOT NULL,
    updated DOUBLE PRECISION NOT NULL
);
-- Долгая память ИИ-NPC: копящиеся воспоминания о конкретном игроке (Фаза 3).
-- Ранжирование при выборке живёт в ai/memory.py (лексика+свежесть); схема уже
-- готова к будущему pgvector — понадобится лишь добавить колонку embedding и
-- заменить одну функцию ранжирования, сама таблица не изменится.
CREATE TABLE IF NOT EXISTS npc_memories (
    uid     BIGINT NOT NULL,
    npc_id  TEXT   NOT NULL,
    ts      DOUBLE PRECISION NOT NULL,
    text    TEXT   NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_npc_mem_uid_npc ON npc_memories(uid, npc_id);
-- ───────── Этап 3.1: транзакционный аукцион и журнал экономики ─────────
-- auction_listings — источник истины по лотам (вместо снапшота в kv_state раз
-- в 60с). Статус active/sold/cancelled; индекс по status — для витрины.
-- economy_ledger — двойная запись всех золото-движений аукциона. operation_id
-- (PRIMARY KEY) даёт идемпотентность: повтор callback'а с тем же op_id не
-- задваивает балансы. Строка комиссии пишется на uid=0 (сток золота). Ядро
-- операций над этими таблицами — engine/econ_tx.py (одна транзакция на операцию).
CREATE TABLE IF NOT EXISTS auction_listings (
    lot_id  TEXT PRIMARY KEY,
    seller  BIGINT NOT NULL,
    item    TEXT   NOT NULL,
    price   BIGINT NOT NULL,
    status  TEXT   NOT NULL DEFAULT 'active',
    created DOUBLE PRECISION,
    closed  DOUBLE PRECISION,
    buyer   BIGINT
);
CREATE INDEX IF NOT EXISTS idx_auction_status ON auction_listings(status);
CREATE TABLE IF NOT EXISTS economy_ledger (
    operation_id TEXT PRIMARY KEY,
    uid          BIGINT NOT NULL,
    operation    TEXT   NOT NULL,
    gold_delta   BIGINT NOT NULL,
    item         TEXT,
    counterparty BIGINT,
    created      DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_ledger_uid ON economy_ledger(uid);
CREATE TABLE IF NOT EXISTS max_shop_intents (
    token TEXT PRIMARY KEY,
    uid BIGINT NOT NULL,
    generation BIGINT NOT NULL,
    room TEXT NOT NULL,
    vendor TEXT NOT NULL,
    item TEXT NOT NULL,
    price BIGINT NOT NULL CHECK (price > 0),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','done','cancelled')),
    receipt TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT now() + interval '5 minutes'
);
CREATE INDEX IF NOT EXISTS idx_max_shop_uid ON max_shop_intents(uid, status);
-- ───────── Этап 3.2: гильдии и гильд-банк ─────────
-- guilds/guild_members — источник истины по гильдиям (вместо guilds.json, чья
-- запись глотала ошибки). Банк (bank_gold/bank_items) меняется транзакционно в
-- engine/guild_tx.py с двойной записью в economy_ledger (ref=gid). guild_members.uid
-- — PRIMARY KEY: игрок состоит максимум в одной гильдии; индекс по gid — для сборки
-- состава при старте (load_guilds).
CREATE TABLE IF NOT EXISTS guilds (
    gid        TEXT PRIMARY KEY,
    name       TEXT,
    leader     BIGINT,
    bank_gold  BIGINT NOT NULL DEFAULT 0,
    bank_items JSONB  NOT NULL DEFAULT '[]',
    created    DOUBLE PRECISION
);
CREATE TABLE IF NOT EXISTS guild_members (
    uid    BIGINT PRIMARY KEY,
    gid    TEXT   NOT NULL,
    rank   TEXT   NOT NULL DEFAULT 'member',
    joined DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_guild_members_gid ON guild_members(gid);
CREATE TABLE IF NOT EXISTS guild_invites (
    uid BIGINT PRIMARY KEY,
    gid TEXT NOT NULL
);
-- ───────── Этап 7.1: аналитика воронки + deep-link атрибуция ─────────
-- analytics_events — сырой лог событий воронки (engine/analytics.py: track()
-- копит в памяти, flush_to_db() пишет батчем тем же тактом, что и персонажей —
-- см. snapshot_worker в bot/main.py). uid не PK — одно uid даёт много строк.
-- Индексы: (event, created) — под выборку шага воронки за период; (uid) —
-- под retention-джойн (character_created -> session_start) по игроку.
CREATE TABLE IF NOT EXISTS analytics_events (
    id      BIGSERIAL PRIMARY KEY,
    uid     BIGINT,
    event   TEXT NOT NULL,
    props   JSONB NOT NULL DEFAULT '{}',
    created DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analytics_event_created ON analytics_events(event, created);
CREATE INDEX IF NOT EXISTS idx_analytics_uid ON analytics_events(uid);
-- attribution — источник трафика игрока по deep-link (/start ref_.../src_...).
-- first_* фиксируется один раз (первый когда-либо /start), last_* обновляется
-- на КАЖДЫЙ /start — так видно и «откуда пришёл», и «что вернуло в последний
-- раз» (реактивация через другую кампанию).
CREATE TABLE IF NOT EXISTS attribution (
    uid          BIGINT PRIMARY KEY,
    first_source TEXT,
    first_ts     DOUBLE PRECISION,
    last_source  TEXT,
    last_ts      DOUBLE PRECISION
);
-- ───────── Этап 7.2: модерация (баны/муты) ─────────
-- Источник истины по банам/мутам. Рантайм читает из кэша в памяти
-- (engine/moderation.py: load() на старте), а сюда пишет каждое действие
-- админа (ban/unban/mute/unmute) — чтобы токсичного игрока можно было
-- остановить без ручной правки БД и пережить перезапуск. muted_until — unix
-- (0 = не замучен); banned — полный запрет. Журнал самих действий — в audit_log.
CREATE TABLE IF NOT EXISTS moderation (
    uid         BIGINT PRIMARY KEY,
    banned      BOOLEAN NOT NULL DEFAULT FALSE,
    muted_until DOUBLE PRECISION NOT NULL DEFAULT 0,
    reason      TEXT,
    by_admin    BIGINT,
    updated     DOUBLE PRECISION
);
-- ───────── Этап 8: журнал вызовов LLM (ai/llmlog.py) ─────────
-- Одна строка на КАЖДОЕ реальное обращение к модели. Копится в памяти
-- (ai/llmlog.py: record()), пишется батчем тем же тактом, что и analytics_events
-- (snapshot_worker). Делает стоимость на DAU измеримой (сумма cost_est за день)
-- и питает дневной HARD-бюджет (ai/cost.py:BUDGET_GUARD). uid не хранится — журнал
-- про модель/деньги, а не про игрока. Индексы: (created) — расход за период;
-- (context, created) — где именно LLM буксует. Без пула (pool=None) — no-op.
CREATE TABLE IF NOT EXISTS llm_log (
    id         BIGSERIAL PRIMARY KEY,
    provider   TEXT NOT NULL,
    model      TEXT NOT NULL,
    tier       TEXT,
    latency_ms INT,
    tokens_in  INT,
    tokens_out INT,
    cost_est   DOUBLE PRECISION,
    outcome    TEXT,
    context    TEXT,
    version    TEXT,
    created    DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_log_created ON llm_log(created);
CREATE INDEX IF NOT EXISTS idx_llm_log_ctx ON llm_log(context, created);
"""


class Database:
    def __init__(self, dsn: str = None):
        self._character_write_locks = {}
        self.dsn = dsn or os.environ.get("DATABASE_URL", "postgresql://localhost/mud")
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self):
        if asyncpg is None:
            raise RuntimeError("asyncpg не установлен — запуск без сохранения")
        self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=10)
        async with self.pool.acquire() as con:
            await con.execute(SCHEMA)
            # миграции для существующих БД
            await con.execute(
                "ALTER TABLE characters ADD COLUMN IF NOT EXISTS race TEXT NOT NULL DEFAULT 'human'")
            await con.execute(
                "ALTER TABLE characters ADD COLUMN IF NOT EXISTS last_seen TIMESTAMPTZ")
            await con.execute(
                "ALTER TABLE characters ADD COLUMN IF NOT EXISTS notify_blocked BOOLEAN NOT NULL DEFAULT FALSE")
            await con.execute(
                "ALTER TABLE characters ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ")
            # Аудит-2а.1: поколение. DEFAULT 1 безопасен для существующих строк —
            # все они получают generation=1 и продолжают писаться save() как обычно.
            await con.execute(
                "ALTER TABLE characters ADD COLUMN IF NOT EXISTS generation BIGINT NOT NULL DEFAULT 1")
            # Изученные умения и порядок боевой панели — постоянный прогресс,
            # а не рантайм боя. Старые строки получают []: при загрузке им
            # выдаются только базовые умения, без угадывания оплаченного обучения.
            await con.execute(
                "ALTER TABLE characters ADD COLUMN IF NOT EXISTS learned JSONB NOT NULL DEFAULT '[]'")
            await con.execute(
                "ALTER TABLE characters ADD COLUMN IF NOT EXISTS loadout JSONB NOT NULL DEFAULT '[]'")
            # Только существующие положительные uid: все они созданы Telegram-
            # ботом. Идемпотентный backfill не меняет персонажей и не присваивает
            # Telegram-адрес будущим MAX-героям с отрицательным внутренним uid.
            await con.execute("""
                INSERT INTO platform_identities (platform, external_user_id, uid)
                SELECT 'telegram', c.uid::text, c.uid FROM characters c
                WHERE c.uid > 0
                ON CONFLICT DO NOTHING
            """)
            # Этап 3.2: колонка ref — по ней в economy_ledger видны ВСЕ движения
            # конкретной гильдии (ref=gid). У аукциона (econ_tx) остаётся NULL.
            await con.execute(
                "ALTER TABLE economy_ledger ADD COLUMN IF NOT EXISTS ref TEXT")
            await con.execute(
                "CREATE INDEX IF NOT EXISTS idx_ledger_ref ON economy_ledger(ref)")
            # Этап 7.2: таблица модерации создаётся в SCHEMA выше; ALTER на случай
            # старой БД без неё — SCHEMA идемпотентна (CREATE IF NOT EXISTS), а
            # отдельных колоночных миграций тут не нужно (новая таблица).
            # ───────── Аудит-2б.1: уникальность активных имён ─────────
            await con.execute(
                "ALTER TABLE characters ADD COLUMN IF NOT EXISTS name_norm TEXT")
            await self._migrate_name_norm(con)

    async def _migrate_name_norm(self, con):
        """БЕЗОПАСНАЯ миграция уникальности имён (Аудит-2б.1). Порядок важен:

        1) backfill name_norm для строк, где он NULL (по name через
           textsafe.name_norm) — БЕЗ этого частичный индекс сравнивал бы NULL'ы;
        2) проверка ДУБЛЕЙ среди активных (deleted_at IS NULL): если есть имена,
           совпадающие по name_norm у нескольких живых персонажей — уникальный
           индекс НЕ создаём (иначе CREATE INDEX упал бы), печатаем предупреждение
           со списком uid и ждём ручного/админ-разрешения. Молчаливого выбора
           «победителя» здесь не делаем — это данные игроков;
        3) индекс создаётся ТОЛЬКО при чистоте — и он гарантирует уникальность
           дальше (конкурентное создание одинаковых имён ловит create_character)."""
        # 1) backfill
        rows = await con.fetch(
            "SELECT uid, name FROM characters WHERE name_norm IS NULL")
        for r in rows:
            await con.execute(
                "UPDATE characters SET name_norm=$2 WHERE uid=$1",
                r["uid"], textsafe.name_norm(r["name"]))
        # 2) дубли среди активных
        dups = await con.fetch(
            "SELECT name_norm, count(*) AS c, array_agg(uid) AS uids "
            "FROM characters WHERE deleted_at IS NULL AND name_norm IS NOT NULL "
            "AND name_norm <> '' GROUP BY name_norm HAVING count(*) > 1")
        if dups:
            for d in dups:
                print(f"[Аудит-2б.1] ДУБЛЬ активного имени name_norm={d['name_norm']!r}: "
                      f"uid={list(d['uids'])} — уникальный индекс НЕ создан, "
                      f"разрешите дубль вручную/через админку и перезапустите.")
            return
        # 3) чистота — создаём частичный уникальный индекс
        await con.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_characters_name_norm "
            "ON characters(name_norm) WHERE deleted_at IS NULL")

    async def close(self):
        if self.pool:
            await self.pool.close()

    @staticmethod
    def _row_to_char(r) -> Character:
        """Собрать Character из строки БД (общий код load_all/find_deleted/restore).
        generation читается из строки (старые БД без колонки не дойдут сюда —
        миграция её добавляет с DEFAULT 1)."""
        ch = Character(
            uid=r["uid"], name=r["name"], cls=r["cls"], race=r["race"], room=r["room"],
            level=r["level"], xp=r["xp"], hp=r["hp"], mp=r["mp"], gold=r["gold"],
            equipment=json.loads(r["equipment"]),
            inventory=json.loads(r["inventory"]),
            quests=json.loads(r["quests"]),
            flags=json.loads(r["flags"]),
            learned=json.loads(r.get("learned", "[]")),
            loadout=json.loads(r.get("loadout", "[]")),
        )
        # Обратная совместимость со строками до миграции. Не вызываем
        # init_skills() для заполненного learned: явно пустая панель игрока
        # должна остаться пустой, а выбранный порядок — неизменным.
        if not ch.learned and not ch.loadout:
            ch.init_skills()
        try:
            ch.generation = int(r["generation"])
        except (KeyError, TypeError):
            ch.generation = 1
        # слоты экипировки на случай новых
        for slot in ("weapon", "armor", "accessory"):
            ch.equipment.setdefault(slot, None)
        return ch

    async def load_all(self) -> Dict[int, Character]:
        """Загрузить всех персонажей в память при старте."""
        out: Dict[int, Character] = {}
        async with self.pool.acquire() as con:
            # мягко удалённые (deleted_at IS NOT NULL) в память не поднимаем
            rows = await con.fetch(
                "SELECT * FROM characters WHERE deleted_at IS NULL")
        for r in rows:
            ch = self._row_to_char(r)
            out[ch.uid] = ch
        return out

    async def save(self, ch: Character):
        async with self._character_write_locks.setdefault(ch.uid, asyncio.Lock()):
            await self._save_character(ch)

    async def set_player_setting(self, ch: Character, key: str, value: str):
        from . import player_settings
        patch = player_settings.patch_for(key, value)
        async with self._character_write_locks.setdefault(ch.uid, asyncio.Lock()):
            async with self.pool.acquire() as con:
                async with con.transaction():
                    row = await con.fetchrow(
                        "SELECT flags, generation, deleted_at FROM characters WHERE uid=$1 FOR UPDATE", ch.uid)
                    if row is None or row['deleted_at'] is not None or int(row['generation']) != ch.generation:
                        raise StaleCharacterWrite('Настройки устаревшего героя нельзя менять.')
                    flags = json.loads(row['flags']) if isinstance(row['flags'], str) else dict(row['flags'])
                    player_settings.apply(flags, patch)
                    await con.execute("UPDATE characters SET flags=$2, updated_at=now() WHERE uid=$1",
                                      ch.uid, json.dumps(flags))
                    if ch.uid < 0 and patch.get('notify', {}).get('push_enabled') is True:
                        await con.execute('UPDATE characters SET notify_blocked=FALSE WHERE uid=$1', ch.uid)
            # Publish after COMMIT, before queued saves serialize the live flags.
            player_settings.apply(ch.flags, patch)

    async def _save_character(self, ch: Character):
        """Сохранить прогресс персонажа. Аудит-2а.1: ТОЛЬКО UPDATE активной строки
        СВОЕГО поколения — строк больше НЕ создаёт (это делает create_character).
        0 затронутых строк → StaleCharacterWrite: объект в памяти устарел
        (персонаж сброшен/поднят в новом поколении, либо мягко удалён). Писать
        его нельзя — иначе воскресим удалённого или затрём нового героя."""
        # An uncertain purchase COMMIT must be reconciled before any snapshot can
        # overwrite its durable balance. Recovery failure blocks this save.
        from .shop_purchase import recover
        await recover(self, ch)
        async with self.pool.acquire() as con:
            status = await con.execute("""
                UPDATE characters SET
                    name=$2, cls=$3, race=$4, room=$5, level=$6, xp=$7, hp=$8, mp=$9, gold=$10,
                    equipment=$11, inventory=$12, quests=$13, flags=$14,
                    learned=$16, loadout=$17, updated_at=now()
                WHERE uid=$1 AND generation=$15 AND deleted_at IS NULL
            """,
                ch.uid, ch.name, ch.cls, ch.race, ch.room, ch.level, ch.xp, ch.hp, ch.mp, ch.gold,
                json.dumps(ch.equipment), json.dumps(ch.inventory),
                json.dumps(ch.quests), json.dumps(ch.flags),
                int(getattr(ch, "generation", 1)),
                json.dumps(ch.learned), json.dumps(ch.loadout),
            )
        if _rowcount(status) == 0:
            raise StaleCharacterWrite(
                f"save() uid={ch.uid} gen={getattr(ch, 'generation', 1)}: "
                "0 строк (устаревший/удалённый персонаж)")

    async def create_character(self, ch: Character) -> int:
        """Атомарно создать/пересоздать персонажа (Аудит-2а.1). Одна транзакция:
        FOR UPDATE по uid; активная строка есть → ActiveCharacterExists; строки
        нет → INSERT generation=1; есть удалённая → generation=старый+1 с полной
        заменой игровых полей и deleted_at=NULL. Пишет audit_log('create').
        Проставляет ch.generation и возвращает фактическое поколение.
        Без пула (dev) → ch.generation=1, только память."""
        if not self.pool:
            ch.generation = 1
            return 1
        equipment = json.dumps(ch.equipment)
        inventory = json.dumps(ch.inventory)
        quests = json.dumps(ch.quests)
        flags = json.dumps(ch.flags)
        learned = json.dumps(ch.learned)
        loadout = json.dumps(ch.loadout)
        nname = textsafe.name_norm(ch.name)   # Аудит-2б.1: ключ уникальности
        try:
            async with self.pool.acquire() as con:
                async with con.transaction():
                    row = await con.fetchrow(
                        "SELECT generation, deleted_at FROM characters WHERE uid=$1 FOR UPDATE",
                        ch.uid)
                    if row is not None and row["deleted_at"] is None:
                        raise ActiveCharacterExists(
                            f"create_character uid={ch.uid}: активный персонаж уже есть")
                    if row is None:
                        gen = 1
                        await con.execute("""
                            INSERT INTO characters
                                (uid,name,cls,race,room,level,xp,hp,mp,gold,
                                 equipment,inventory,quests,flags,updated_at,generation,name_norm,
                                 learned,loadout)
                            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14, now(), $15,$16,$17,$18)
                        """, ch.uid, ch.name, ch.cls, ch.race, ch.room, ch.level, ch.xp,
                            ch.hp, ch.mp, ch.gold, equipment, inventory, quests, flags, gen, nname,
                            learned, loadout)
                    else:
                        gen = int(row["generation"]) + 1
                        await con.execute("""
                            UPDATE characters SET
                                name=$2, cls=$3, race=$4, room=$5, level=$6, xp=$7, hp=$8,
                                mp=$9, gold=$10, equipment=$11, inventory=$12, quests=$13,
                                flags=$14, updated_at=now(), generation=$15, deleted_at=NULL,
                                name_norm=$16, learned=$17, loadout=$18
                            WHERE uid=$1
                        """, ch.uid, ch.name, ch.cls, ch.race, ch.room, ch.level, ch.xp,
                            ch.hp, ch.mp, ch.gold, equipment, inventory, quests, flags, gen, nname,
                            learned, loadout)
                    if ch.uid > 0:
                        # Текущий единственный транспорт создания — Telegram.
                        # MAX сначала резервирует отрицательный uid отдельно.
                        await con.execute("""
                            INSERT INTO platform_identities (platform, external_user_id, uid)
                            VALUES ('telegram', $1, $2) ON CONFLICT DO NOTHING
                        """, str(ch.uid), ch.uid)
                    await con.execute(
                        "INSERT INTO audit_log (ts, uid, action, details) VALUES ($1,$2,$3,$4)",
                        time.time(), ch.uid, "create",
                        json.dumps({"generation": gen, "name": ch.name}))
        except _UniqueViolationError as e:
            # Частичный уникальный индекс по name_norm среди активных: конкурентное
            # создание двух героев с одинаковым именем решает БД (один падает здесь).
            raise NameTaken(
                f"create_character uid={ch.uid}: имя {ch.name!r} занято") from e
        ch.generation = gen
        return gen

    async def resolve_player_id(self, platform: str, external_user_id) -> Optional[int]:
        """Найти внутренний uid по транспорту; имена игроков не участвуют."""
        from .identity import identity_key
        platform, external_user_id = identity_key(platform, external_user_id)
        if not self.pool:
            return None
        async with self.pool.acquire() as con:
            uid = await con.fetchval(
                "SELECT uid FROM platform_identities "
                "WHERE platform=$1 AND external_user_id=$2",
                platform, external_user_id)
        return int(uid) if uid is not None else None

    async def max_external_user_id(self, uid: int) -> Optional[str]:
        """Resolve an internal MAX uid for outbound messages, including after restart."""
        if not self.pool or uid >= 0:
            return None
        return await self.pool.fetchval(
            "SELECT external_user_id FROM platform_identities "
            "WHERE platform='max' AND uid=$1", uid)

    async def enqueue_max_update(self, event_key: str, external_user_id: str,
                                 message_text: str) -> bool:
        """Persist the input before webhook ACK; duplicate deliveries are ignored."""
        if not self.pool:
            raise RuntimeError("MAX webhook requires PostgreSQL")
        row = await self.pool.fetchval(
            "INSERT INTO max_inbox(event_key, external_user_id, message_text) "
            "VALUES($1,$2,$3) ON CONFLICT DO NOTHING RETURNING event_key",
            event_key, external_user_id, message_text)
        return row is not None

    async def claim_next_max_update(self):
        """Claim once before mutating state; never auto-replay an uncertain action."""
        if not self.pool:
            raise RuntimeError("MAX inbox requires PostgreSQL")
        async with self.pool.acquire() as con:
            async with con.transaction():
                row = await con.fetchrow("""
                    SELECT event_key, external_user_id, message_text FROM max_inbox
                    WHERE status='pending' ORDER BY received_at, event_key
                    LIMIT 1 FOR UPDATE SKIP LOCKED
                """)
                if row:
                    await con.execute("UPDATE max_inbox SET status='processing' WHERE event_key=$1",
                                      row["event_key"])
                return dict(row) if row else None

    async def finish_max_update(self, event_key: str, *, failed: bool = False):
        if not self.pool:
            raise RuntimeError("MAX inbox requires PostgreSQL")
        await self.pool.execute(
            "UPDATE max_inbox SET status=$2, message_text=NULL, finished_at=now() "
            "WHERE event_key=$1 AND status='processing'",
            event_key, "failed" if failed else "done")

    async def reserve_max_player_id(self, external_user_id) -> int:
        """Идемпотентно выделить новый uid для MAX без привязки к Telegram.

        Резервирование само по себе не создаёт героя и не объединяет аккаунты.
        Повторное событие MAX возвращает тот же uid; коллизии с легаси-строками
        отрицательного диапазона пропускаются.
        """
        from .identity import identity_key
        _, external_user_id = identity_key("max", external_user_id)
        if not self.pool:
            raise RuntimeError("MAX identity requires PostgreSQL")
        async with self.pool.acquire() as con:
            async with con.transaction():
                existing = await con.fetchval(
                    "SELECT uid FROM platform_identities "
                    "WHERE platform='max' AND external_user_id=$1",
                    external_user_id)
                if existing is not None:
                    return int(existing)
                for _ in range(32):
                    uid = int(await con.fetchval("SELECT nextval('max_player_uid_seq')"))
                    if await con.fetchval("SELECT 1 FROM characters WHERE uid=$1", uid):
                        continue
                    row = await con.fetchrow("""
                        INSERT INTO platform_identities (platform, external_user_id, uid)
                        VALUES ('max', $1, $2)
                        ON CONFLICT DO NOTHING RETURNING uid
                    """, external_user_id, uid)
                    if row:
                        return int(row["uid"])
                    # Конкурентный повтор с тем же MAX ID выиграл вставку.
                    existing = await con.fetchval(
                        "SELECT uid FROM platform_identities "
                        "WHERE platform='max' AND external_user_id=$1",
                        external_user_id)
                    if existing is not None:
                        return int(existing)
        raise RuntimeError("Could not reserve MAX player ID")

    async def reset_character(self, uid: int, expected_generation: int,
                              details: dict = None) -> int:
        """Атомарно мягко сбросить персонажа (Аудит-2а.1). Транзакция: FOR UPDATE;
        активна и generation совпадает → generation+=1 (инвалидирует stale-объекты
        в памяти), deleted_at=now(), audit_log('reset'); несовпадение поколения или
        уже удалён → StaleCharacterWrite; строки нет → CharacterNotFound.
        Возвращает новое поколение. Без пула (dev) → expected+1, только память."""
        if not self.pool:
            return int(expected_generation) + 1
        async with self.pool.acquire() as con:
            async with con.transaction():
                row = await con.fetchrow(
                    "SELECT generation, deleted_at FROM characters WHERE uid=$1 FOR UPDATE",
                    uid)
                if row is None:
                    raise CharacterNotFound(f"reset_character uid={uid}: строки нет")
                if row["deleted_at"] is not None or \
                        int(row["generation"]) != int(expected_generation):
                    raise StaleCharacterWrite(
                        f"reset_character uid={uid}: поколение разошлось "
                        f"(ожидали {expected_generation}, в БД {row['generation']}, "
                        f"deleted={row['deleted_at'] is not None})")
                new_gen = int(row["generation"]) + 1
                from . import econ_tx
                await econ_tx.cancel_for_reset(con, uid, new_gen)
                await con.execute(
                    "UPDATE characters SET generation=$2, deleted_at=now() WHERE uid=$1",
                    uid, new_gen)
                from . import party_store
                await party_store.remove_player(con, uid)
                from . import guild_store
                await guild_store.remove_player(con, uid)
                await con.execute(
                    "INSERT INTO audit_log (ts, uid, action, details) VALUES ($1,$2,$3,$4)",
                    time.time(), uid, "reset", json.dumps(details or {}))
        return new_gen

    async def restore_character(self, uid: int, max_age_sec: int = 86400) -> Character:
        """Атомарно снять мягкое удаление (Аудит-2а.1). Транзакция: FOR UPDATE;
        строки нет → CharacterNotFound; deleted_at IS NULL → ActiveCharacterExists;
        окно max_age_sec истекло → RestoreExpired; иначе deleted_at=NULL (generation
        НЕ меняем — он уже поднят при reset, старые объекты уже невалидны),
        audit_log('restore'). Возвращает Character для помещения в память."""
        if not self.pool:
            raise CharacterNotFound(f"restore_character uid={uid}: нет пула")
        async with self.pool.acquire() as con:
            async with con.transaction():
                row = await con.fetchrow(
                    "SELECT * FROM characters WHERE uid=$1 FOR UPDATE", uid)
                if row is None:
                    raise CharacterNotFound(f"restore_character uid={uid}: строки нет")
                if row["deleted_at"] is None:
                    raise ActiveCharacterExists(
                        f"restore_character uid={uid}: персонаж уже активен")
                fresh = await con.fetchrow(
                    "SELECT 1 FROM characters WHERE uid=$1 AND deleted_at IS NOT NULL "
                    "AND deleted_at > now() - ($2 || ' seconds')::interval",
                    uid, str(int(max_age_sec)))
                if fresh is None:
                    raise RestoreExpired(
                        f"restore_character uid={uid}: окно {max_age_sec}с истекло")
                await con.execute(
                    "UPDATE characters SET deleted_at=NULL WHERE uid=$1", uid)
                await con.execute(
                    "INSERT INTO audit_log (ts, uid, action, details) VALUES ($1,$2,$3,$4)",
                    time.time(), uid, "restore",
                    json.dumps({"name": row["name"], "level": row["level"]}))
        return self._row_to_char(row)

    async def delete(self, uid: int):
        """Жёсткое удаление (оставлено для совместимости). Для /reset используем
        soft_delete — он позволяет восстановить персонажа в течение суток."""
        async with self.pool.acquire() as con:
            await con.execute("DELETE FROM characters WHERE uid=$1", uid)

    async def soft_delete(self, uid: int):
        """Мягко удалить персонажа: проставить deleted_at=now(). Запись остаётся
        в БД (load_all её пропускает), чтобы можно было восстановить (find_deleted).
        Повторный вызов идемпотентен — deleted_at уже стоит, обновится на текущий."""
        async with self.pool.acquire() as con:
            await con.execute(
                "UPDATE characters SET deleted_at = now() WHERE uid=$1", uid)

    async def find_deleted(self, uid: int, max_age_sec: int = 86400) -> Optional[Character]:
        """Найти НЕДАВНО мягко удалённого персонажа (deleted_at моложе max_age_sec).
        Возвращает Character для предложения восстановления или None.
        Без пула (pool=None) — None."""
        if not self.pool:
            return None
        async with self.pool.acquire() as con:
            r = await con.fetchrow(
                "SELECT * FROM characters WHERE uid=$1 AND deleted_at IS NOT NULL "
                "AND deleted_at > now() - ($2 || ' seconds')::interval",
                uid, str(int(max_age_sec)))
        if not r:
            return None
        return self._row_to_char(r)

    async def restore_deleted(self, uid: int) -> Optional[Character]:
        """Легаси-обёртка над атомарным restore_character (Аудит-2а.1): глотает
        ошибки жизненного цикла и возвращает None, сохраняя прежний контракт
        (Character | None). Новый код в bot зовёт restore_character напрямую,
        чтобы отличать RestoreExpired от «нечего восстанавливать»."""
        try:
            return await self.restore_character(uid)
        except (CharacterNotFound, ActiveCharacterExists, RestoreExpired):
            return None

    async def add_audit(self, uid: int, action: str, details: dict = None):
        """Записать событие аудита (напр. action='reset'/'restore'). Без пула
        (pool=None) — no-op. Ошибка записи в журнал не должна ронять действие."""
        if not self.pool:
            return
        try:
            async with self.pool.acquire() as con:
                await con.execute(
                    "INSERT INTO audit_log (ts, uid, action, details) VALUES ($1,$2,$3,$4)",
                    time.time(), uid, action,
                    json.dumps(details or {}))
        except Exception:
            pass

    async def recent_audit(self, action: str, limit: int = 20) -> List[dict]:
        """Последние записи журнала по типу действия -> [{ts, uid, details}, ...].

        Нужен админке: отчёты о багах (action='bug') копятся в audit_log, и без
        чтения их видно только в личке админа — а личку легко пролистать или
        потерять, если админов несколько или бот перезапускался.
        Сортировка по ts DESC; без пула — пустой список (админка это переживёт).
        """
        if not self.pool:
            return []
        try:
            async with self.pool.acquire() as con:
                rows = await con.fetch(
                    "SELECT ts, uid, details FROM audit_log WHERE action = $1 "
                    "ORDER BY ts DESC LIMIT $2", action, int(limit))
        except Exception:
            return []
        out = []
        for r in rows:
            d = r["details"]
            if isinstance(d, str):          # JSONB иногда приходит строкой
                try:
                    d = json.loads(d)
                except Exception:
                    d = {}
            out.append({"ts": float(r["ts"]), "uid": int(r["uid"]), "details": d or {}})
        return out

    # ───────── Этап 7.2: модерация (баны/муты) + компенсации ─────────
    # Кэш решает гейты в рантайме (engine/moderation.py); эти методы — только
    # персист/загрузка. Без пула (pool=None) — тихая деградация (память в moderation).
    async def load_moderation(self) -> List[dict]:
        """Все строки модерации -> [{uid,banned,muted_until,reason,by_admin,updated}, ...]."""
        if not self.pool:
            return []
        async with self.pool.acquire() as con:
            rows = await con.fetch(
                "SELECT uid, banned, muted_until, reason, by_admin, updated FROM moderation")
        return [dict(r) for r in rows]

    async def get_moderation(self, uid: int) -> Optional[dict]:
        """Строка модерации по uid (или None)."""
        if not self.pool:
            return None
        async with self.pool.acquire() as con:
            r = await con.fetchrow(
                "SELECT uid, banned, muted_until, reason, by_admin, updated "
                "FROM moderation WHERE uid=$1", uid)
        return dict(r) if r else None

    async def set_moderation(self, uid: int, banned: bool, muted_until: float,
                             reason: str = "", by_admin: int = 0):
        """Upsert состояния модерации игрока (идемпотентно). Без пула — no-op."""
        if not self.pool:
            return
        async with self.pool.acquire() as con:
            await con.execute("""
                INSERT INTO moderation (uid, banned, muted_until, reason, by_admin, updated)
                VALUES ($1,$2,$3,$4,$5,$6)
                ON CONFLICT (uid) DO UPDATE SET
                    banned=$2, muted_until=$3, reason=$4, by_admin=$5, updated=$6
            """, uid, bool(banned), float(muted_until), reason or "",
                int(by_admin or 0), time.time())

    async def set_moderation_with_audit(self, uid: int, banned: bool, muted_until: float,
                                        reason: str, by_admin: int, action: str,
                                        details: dict = None):
        """Персист бана/мута И запись в audit_log — ОДНОЙ транзакцией (Аудит-2а.2).

        Раньше engine/moderation.py звало set_moderation() и add_audit() по
        отдельности, каждый со своим `except Exception: pass` — сбой одной из
        двух записей (напр. обрыв соединения между ними) мог оставить кэш в
        памяти помеченным «забанен», а в БД — ни строки moderation, ни следа в
        audit_log (fail-open: рестарт тихо снимал бан). Здесь UPDATE/UPSERT
        moderation и INSERT audit_log — в ОДНОЙ транзакции: любая ошибка →
        откат ОБОИХ, исключение уходит НАРУЖУ вызывающему (никакого try/except
        здесь — engine/moderation.py решает, что делать со сбоем: кэш меняется
        только после успешного commit).

        Без пула (dev, pool=None) — no-op (осознанная деградация в память,
        см. докстринг engine/moderation.py); в этом случае исключения не
        будет — вызывающая сторона просто продолжит работать с кэшем."""
        if not self.pool:
            return
        async with self.pool.acquire() as con:
            async with con.transaction():
                await con.execute("""
                    INSERT INTO moderation (uid, banned, muted_until, reason, by_admin, updated)
                    VALUES ($1,$2,$3,$4,$5,$6)
                    ON CONFLICT (uid) DO UPDATE SET
                        banned=$2, muted_until=$3, reason=$4, by_admin=$5, updated=$6
                """, uid, bool(banned), float(muted_until), reason or "",
                    int(by_admin or 0), time.time())
                await con.execute(
                    "INSERT INTO audit_log (ts, uid, action, details) VALUES ($1,$2,$3,$4)",
                    time.time(), uid, action, json.dumps(details or {}))

    async def grant_gold(self, uid: int, amount: int, op_id: str,
                         operation: str = "compensation", by_admin: int = 0) -> Optional[int]:
        """Начислить золото игроку одной транзакцией с записью в economy_ledger
        (идемпотентно по op_id — повтор callback'а не задвоит). Возвращает новый
        баланс или None (нет персонажа / повтор / без пула). Журнал экономики —
        обязателен: каждая компенсация оставляет след (operation, counterparty=admin)."""
        if not self.pool:
            return None
        async with self.pool.acquire() as con:
            # быстрый путь идемпотентности
            if await con.fetchrow(
                    "SELECT 1 FROM economy_ledger WHERE operation_id=$1", op_id):
                return None
            try:
                async with con.transaction():
                    row = await con.fetchrow(
                        "SELECT gold FROM characters WHERE uid=$1 FOR UPDATE", uid)
                    if row is None:
                        return None
                    new_gold = int(row["gold"]) + int(amount)
                    await con.execute(
                        "UPDATE characters SET gold=$1 WHERE uid=$2", new_gold, uid)
                    await con.execute(
                        "INSERT INTO economy_ledger "
                        "(operation_id, uid, operation, gold_delta, item, counterparty, created) "
                        "VALUES ($1,$2,$3,$4,$5,$6,$7)",
                        op_id, uid, operation, int(amount), None,
                        int(by_admin or 0), time.time())
                    return new_gold
            except Exception:
                return None

    async def ledger_recent(self, uid: int, limit: int = 15) -> List[dict]:
        """Последние записи economy_ledger игрока (для админ-леджера). Без пула — []."""
        if not self.pool:
            return []
        async with self.pool.acquire() as con:
            rows = await con.fetch(
                "SELECT operation, gold_delta, item, counterparty, created "
                "FROM economy_ledger WHERE uid=$1 ORDER BY created DESC LIMIT $2",
                uid, int(limit))
        return [dict(r) for r in rows]

    async def find_by_name(self, name: str) -> List[dict]:
        """Найти АКТИВНЫХ персонажей по имени (Аудит-2б.1). Возвращает СПИСОК
        совпадений [{uid,name,level,gold,room}, ...] (для админки).

        Раньше был LIMIT 1 — при неоднозначном имени молча брал случайного.
        Теперь сравнение по name_norm (NFKC→casefold→схлоп): с уникальным индексом
        активных дублей быть не должно (список из 0/1), но легаси-дубли ДО их
        разрешения возможны — тогда список длиннее 1, и вызывающий (bot) просит
        точный uid. lower(name) оставлен запасным сравнением на случай строк с
        ещё не заполненным name_norm. Без пула — []."""
        if not self.pool:
            return []
        nn = textsafe.name_norm(name)
        async with self.pool.acquire() as con:
            rows = await con.fetch(
                "SELECT uid, name, level, gold, room FROM characters "
                "WHERE deleted_at IS NULL AND (name_norm=$2 OR lower(name)=lower($1)) "
                "ORDER BY uid", name, nn)
        return [dict(r) for r in rows]

    # ───────── push-реактивация ─────────
    # Деградация: без пула (pool=None) все методы тихо возвращают пустоту.
    async def list_notify_targets(self, exclude_recent_sec: int = None) -> List[tuple]:
        """uid всех персонажей, не заблокировавших бота -> [(uid,), ...].

        exclude_recent_sec: если задан, исключить тех, кто был замечен
        (last_seen) позже, чем now() - интервал — они и так сейчас в игре
        и получат внутриигровой анонс напрямую, повторный push им не нужен."""
        if not self.pool:
            return []
        if exclude_recent_sec is not None:
            async with self.pool.acquire() as con:
                rows = await con.fetch(
                    "SELECT uid FROM characters WHERE notify_blocked = FALSE AND deleted_at IS NULL "
                    "AND (last_seen IS NULL OR last_seen < now() - ($1 || ' seconds')::interval)",
                    str(int(exclude_recent_sec)))
            return [(r["uid"],) for r in rows]
        async with self.pool.acquire() as con:
            rows = await con.fetch(
                "SELECT uid FROM characters WHERE notify_blocked = FALSE AND deleted_at IS NULL")
        return [(r["uid"],) for r in rows]

    async def mark_notify_blocked(self, uid: int):
        """Пометить: юзер заблокировал бота (403) — больше не слать."""
        if not self.pool:
            return
        async with self.pool.acquire() as con:
            await con.execute(
                "UPDATE characters SET notify_blocked = TRUE WHERE uid=$1", uid)

    async def touch_last_seen(self, uid: int):
        if not self.pool:
            return
        async with self.pool.acquire() as con:
            await con.execute(
                "UPDATE characters SET last_seen = now() WHERE uid=$1", uid)

    async def upsert_schedule(self, uid: int, category: str,
                              fire_at: float, payload: str = None):
        """Запланировать отложенный push (uid+category — уникальны)."""
        if not self.pool:
            return
        async with self.pool.acquire() as con:
            await con.execute("""
                INSERT INTO notify_schedule (uid, category, fire_at, payload)
                VALUES ($1,$2,$3,$4)
                ON CONFLICT (uid, category) DO UPDATE SET
                    fire_at=$3, payload=$4
            """, uid, category, float(fire_at), payload)

    async def pop_due_schedule(self, now: float) -> List[tuple]:
        """Забрать и удалить созревшие записи -> [(uid, category, payload), ...].
        Ограничено LIMIT 200 за проход (упорядочено по fire_at), чтобы не
        выгребать разом всю очередь при большом бэклоге — воркер тикает
        каждую минуту, остаток заберёт на следующем проходе."""
        if not self.pool:
            return []
        async with self.pool.acquire() as con:
            rows = await con.fetch(
                "DELETE FROM notify_schedule WHERE (uid, category) IN ("
                "  SELECT uid, category FROM notify_schedule"
                "  WHERE fire_at <= $1"
                "  ORDER BY fire_at ASC LIMIT 200"
                ") RETURNING uid, category, payload", float(now))
        return [(r["uid"], r["category"], r["payload"]) for r in rows]

    async def log_notify(self, uid: int, category: str, ok: bool):
        """Журнал фактических отправок push (fire-and-forget: без пула — тихо
        деградирует, ошибка записи в лог не должна ронять доставку)."""
        if not self.pool:
            return
        try:
            async with self.pool.acquire() as con:
                await con.execute(
                    "INSERT INTO notify_log (uid, category, ts, ok) VALUES ($1,$2,$3,$4)",
                    uid, category, time.time(), bool(ok))
        except Exception:
            pass

    # ───────── key-value рантайм-состояние (kv_state) ─────────
    # Мировой снапшот, таймеры боссов, аукцион, территории. Без пула (pool=None)
    # — no-op на запись и None на чтение: игра работает на файловом/памятном
    # fallback ровно как раньше (обратная совместимость сохранена).
    async def kv_set(self, k: str, value: dict):
        """Записать/обновить JSON-значение по ключу k."""
        if not self.pool:
            return
        async with self.pool.acquire() as con:
            await con.execute("""
                INSERT INTO kv_state (k, v, updated) VALUES ($1, $2, $3)
                ON CONFLICT (k) DO UPDATE SET v=$2, updated=$3
            """, k, json.dumps(value), time.time())

    async def kv_get(self, k: str) -> Optional[dict]:
        """Прочитать JSON-значение по ключу k (или None, если нет/без пула)."""
        if not self.pool:
            return None
        async with self.pool.acquire() as con:
            row = await con.fetchrow("SELECT v FROM kv_state WHERE k=$1", k)
        if not row or row["v"] is None:
            return None
        v = row["v"]
        # asyncpg отдаёт JSONB как строку — разбираем; но переживём и dict/список
        return json.loads(v) if isinstance(v, (str, bytes)) else v

    # ───────── долгая память NPC (npc_memories, Фаза 3) ─────────
    # Копящиеся воспоминания ИИ-NPC о конкретном игроке. Без пула (pool=None)
    # — тихая деградация: add — no-op, get — [], как и остальные методы этого
    # слоя (см. kv_*/notify_* выше). Ранжирование при выборке — в ai/memory.py.
    async def add_npc_memory(self, uid: int, npc_id: str, text: str):
        """Добавить запись воспоминания и подрезать хвост сверх лимита (prune)."""
        if not self.pool:
            return
        async with self.pool.acquire() as con:
            await con.execute(
                "INSERT INTO npc_memories (uid, npc_id, ts, text) VALUES ($1,$2,$3,$4)",
                uid, npc_id, time.time(), text)
        await self.prune_npc_memories(uid, npc_id)

    async def get_npc_memories(self, uid: int, npc_id: str, limit: int = 20) -> List[tuple]:
        """Воспоминания NPC об игроке -> [(ts, text), ...], свежие первыми."""
        if not self.pool:
            return []
        async with self.pool.acquire() as con:
            rows = await con.fetch(
                "SELECT ts, text FROM npc_memories WHERE uid=$1 AND npc_id=$2 "
                "ORDER BY ts DESC LIMIT $3", uid, npc_id, int(limit))
        return [(r["ts"], r["text"]) for r in rows]

    async def prune_npc_memories(self, uid: int, npc_id: str, keep: int = 40):
        """Удалить старейшие записи сверх keep штук для пары (uid, npc_id)."""
        if not self.pool:
            return
        async with self.pool.acquire() as con:
            await con.execute("""
                DELETE FROM npc_memories WHERE ctid IN (
                    SELECT ctid FROM npc_memories WHERE uid=$1 AND npc_id=$2
                    ORDER BY ts DESC OFFSET $3
                )
            """, uid, npc_id, int(keep))

    # ───────── Этап 7.1: аналитика воронки + deep-link атрибуция ─────────
    async def add_events_batch(self, rows: List[dict]):
        """Записать батч событий аналитики (engine/analytics.py: track()+flush()).
        rows — [{'uid':int,'event':str,'props':dict,'ts':float}, ...]. Fire-and-
        forget: ошибка записи не должна ронять снапшот-воркер бота. Без пула
        (pool=None) — no-op."""
        if not self.pool or not rows:
            return
        try:
            async with self.pool.acquire() as con:
                await con.executemany(
                    "INSERT INTO analytics_events (uid, event, props, created) "
                    "VALUES ($1,$2,$3,$4)",
                    [(r.get("uid"), r.get("event"), json.dumps(r.get("props") or {}),
                      float(r.get("ts") or time.time())) for r in rows])
        except Exception:
            pass

    # ───────── Этап 8: журнал вызовов LLM (llm_log) ─────────
    async def add_llm_batch(self, rows: List[dict]):
        """Записать батч записей журнала LLM (ai/llmlog.py: record()+flush()).
        rows — [{'provider','model','tier','latency_ms','tokens_in','tokens_out',
        'cost_est','outcome','context','version','ts'}, ...]. Fire-and-forget:
        ошибка записи не должна ронять snapshot_worker. Без пула — no-op."""
        if not self.pool or not rows:
            return
        try:
            async with self.pool.acquire() as con:
                await con.executemany(
                    "INSERT INTO llm_log (provider, model, tier, latency_ms, "
                    "tokens_in, tokens_out, cost_est, outcome, context, version, created) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
                    [(str(r.get("provider") or "?"), str(r.get("model") or "?"),
                      r.get("tier"), int(r.get("latency_ms") or 0),
                      int(r.get("tokens_in") or 0), int(r.get("tokens_out") or 0),
                      float(r.get("cost_est") or 0.0), r.get("outcome"),
                      r.get("context"), r.get("version"),
                      float(r.get("ts") or time.time())) for r in rows])
        except Exception:
            pass

    async def get_attribution(self, uid: int) -> Optional[dict]:
        """Строка атрибуции игрока (источник первого/последнего /start) ->
        {'first_source','first_ts','last_source','last_ts'} или None.
        Используется экспортом данных игрока (/delete_me → «Мои данные»,
        Этап 10). Без пула — None."""
        if not self.pool:
            return None
        async with self.pool.acquire() as con:
            r = await con.fetchrow(
                "SELECT first_source, first_ts, last_source, last_ts "
                "FROM attribution WHERE uid=$1", uid)
        return dict(r) if r else None

    async def upsert_attribution(self, uid: int, source: str):
        """Зафиксировать источник трафика игрока на /start. first_source/
        first_ts пишутся ТОЛЬКО при первой строке (ON CONFLICT их не трогает,
        см. фиксированные значения в VALUES вместо повторной вставки $2/$3
        в обновляемые колонки); last_source/last_ts обновляются при каждом
        вызове. Без пула (pool=None) — no-op."""
        if not self.pool:
            return
        ts = time.time()
        try:
            async with self.pool.acquire() as con:
                await con.execute("""
                    INSERT INTO attribution (uid, first_source, first_ts, last_source, last_ts)
                    VALUES ($1, $2, $3, $2, $3)
                    ON CONFLICT (uid) DO UPDATE SET last_source=$2, last_ts=$3
                """, uid, source, ts)
        except Exception:
            pass
