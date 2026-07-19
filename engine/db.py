# -*- coding: utf-8 -*-
"""
Слой хранения на PostgreSQL (asyncpg).
Персонажи сериализуются: простые поля — в колонки, сложные — в JSONB.
Мобы/респавн — это рантайм-состояние мира, в БД не пишем (живёт в памяти).
"""
import json
import os
import time
from typing import Dict, List, Optional

try:
    import asyncpg
except ImportError:
    asyncpg = None   # БД опциональна: без asyncpg игра идёт без сохранения

from .character import Character

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/mud")

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
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen  TIMESTAMPTZ,
    notify_blocked BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_at TIMESTAMPTZ
);
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
-- 'boss_last', 'auction', 'territory'), значение — цельный JSON.
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
            # Этап 3.2: колонка ref — по ней в economy_ledger видны ВСЕ движения
            # конкретной гильдии (ref=gid). У аукциона (econ_tx) остаётся NULL.
            await con.execute(
                "ALTER TABLE economy_ledger ADD COLUMN IF NOT EXISTS ref TEXT")
            await con.execute(
                "CREATE INDEX IF NOT EXISTS idx_ledger_ref ON economy_ledger(ref)")
            # Этап 7.2: таблица модерации создаётся в SCHEMA выше; ALTER на случай
            # старой БД без неё — SCHEMA идемпотентна (CREATE IF NOT EXISTS), а
            # отдельных колоночных миграций тут не нужно (новая таблица).

    async def close(self):
        if self.pool:
            await self.pool.close()

    async def load_all(self) -> Dict[int, Character]:
        """Загрузить всех персонажей в память при старте."""
        out: Dict[int, Character] = {}
        async with self.pool.acquire() as con:
            # мягко удалённые (deleted_at IS NOT NULL) в память не поднимаем
            rows = await con.fetch(
                "SELECT * FROM characters WHERE deleted_at IS NULL")
        for r in rows:
            ch = Character(
                uid=r["uid"], name=r["name"], cls=r["cls"], race=r["race"], room=r["room"],
                level=r["level"], xp=r["xp"], hp=r["hp"], mp=r["mp"], gold=r["gold"],
                equipment=json.loads(r["equipment"]),
                inventory=json.loads(r["inventory"]),
                quests=json.loads(r["quests"]),
                flags=json.loads(r["flags"]),
            )
            # слоты экипировки на случай новых
            for slot in ("weapon", "armor", "accessory"):
                ch.equipment.setdefault(slot, None)
            out[ch.uid] = ch
        return out

    async def save(self, ch: Character):
        async with self.pool.acquire() as con:
            await con.execute("""
                INSERT INTO characters
                    (uid,name,cls,race,room,level,xp,hp,mp,gold,equipment,inventory,quests,flags,updated_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14, now())
                ON CONFLICT (uid) DO UPDATE SET
                    name=$2, cls=$3, race=$4, room=$5, level=$6, xp=$7, hp=$8, mp=$9, gold=$10,
                    equipment=$11, inventory=$12, quests=$13, flags=$14, updated_at=now()
            """,
                ch.uid, ch.name, ch.cls, ch.race, ch.room, ch.level, ch.xp, ch.hp, ch.mp, ch.gold,
                json.dumps(ch.equipment), json.dumps(ch.inventory),
                json.dumps(ch.quests), json.dumps(ch.flags),
            )

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
        ch = Character(
            uid=r["uid"], name=r["name"], cls=r["cls"], race=r["race"], room=r["room"],
            level=r["level"], xp=r["xp"], hp=r["hp"], mp=r["mp"], gold=r["gold"],
            equipment=json.loads(r["equipment"]),
            inventory=json.loads(r["inventory"]),
            quests=json.loads(r["quests"]),
            flags=json.loads(r["flags"]),
        )
        for slot in ("weapon", "armor", "accessory"):
            ch.equipment.setdefault(slot, None)
        return ch

    async def restore_deleted(self, uid: int) -> Optional[Character]:
        """Снять мягкое удаление (deleted_at=NULL) и вернуть загруженного персонажа
        для помещения обратно в память. None, если восстанавливать нечего."""
        ch = await self.find_deleted(uid)
        if ch is None:
            return None
        async with self.pool.acquire() as con:
            await con.execute(
                "UPDATE characters SET deleted_at = NULL WHERE uid=$1", uid)
        return ch

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

    async def find_by_name(self, name: str) -> Optional[dict]:
        """Найти персонажа по имени (регистронезависимо, первое совпадение).
        Возвращает {uid,name,level,gold,room} или None. Для поиска в админке."""
        if not self.pool:
            return None
        async with self.pool.acquire() as con:
            r = await con.fetchrow(
                "SELECT uid, name, level, gold, room FROM characters "
                "WHERE lower(name)=lower($1) AND deleted_at IS NULL LIMIT 1", name)
        return dict(r) if r else None

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
                    "SELECT uid FROM characters WHERE notify_blocked = FALSE "
                    "AND (last_seen IS NULL OR last_seen < now() - ($1 || ' seconds')::interval)",
                    str(int(exclude_recent_sec)))
            return [(r["uid"],) for r in rows]
        async with self.pool.acquire() as con:
            rows = await con.fetch(
                "SELECT uid FROM characters WHERE notify_blocked = FALSE")
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
