# -*- coding: utf-8 -*-
"""
Транзакционное ядро гильдий и гильд-банка (Этап 3.2).

Зачем: раньше гильдии жили в guilds.json, а GuildManager.save() глотал любые
ошибки записи (`except Exception: pass`) — при падении процесса между списанием
золота у игрока и записью файла вклад в казну терялся. Здесь каждая операция над
банком — одна атомарная БД-транзакция с двойной записью в economy_ledger, поэтому
золото/предмет не пропадают и не удваиваются даже при обрыве на середине.

Слой без Telegram (engine/): по образцу engine/econ_tx.py все функции принимают
cf (conn_factory) — async-контекст-менеджер, отдающий соединение с интерфейсом
asyncpg (.transaction()/.execute()/.fetchrow()/.fetch()). В бою это db.pool.acquire,
в тестах — мок без Postgres (см. test_guild_tx.py). Хелперы блокировки/записи
персонажа, разбор JSONB, идемпотентность и детектор дубликата PK
ПЕРЕИСПОЛЬЗУЮТСЯ из econ_tx — ядро экономики одно, обе системы пишут в один и тот
же economy_ledger.

Порядок блокировок (профилактика дедлока): гильд-операция трогает РОВНО одного
персонажа и РОВНО одну гильдию и всегда берёт FOR UPDATE в детерминированном
порядке — СНАЧАЛА characters(uid), ПОТОМ guilds(gid). Встречных порядков в коде
нет нигде, поэтому взаимной блокировки не возникает.

Идемпотентность: как в econ_tx — каждая операция кладёт строку(и) в economy_ledger
с operation_id = op_id (PRIMARY KEY). Повтор того же op_id (ретрай callback'а или
двойной клик в ту же секунду) не меняет балансы дважды: быстрый путь —
предварительный SELECT по operation_id; страховка от гонки — сам PRIMARY KEY
(duplicate key → откат транзакции → идемпотентный ответ).

Как гильдия ложится в economy_ledger (Этап 3.2 добавил колонку ref TEXT):
  • gid операции пишем в ОТДЕЛЬНУЮ колонку ref (а не в item) — расследуемость:
    WHERE ref='gid' поднимает ВСЕ движения гильдии. Колонка item остаётся под
    предмет ИЛИ NULL (для золото-операций — NULL). econ_tx ref не заполняет (NULL).
  • ЗОЛОТО (deposit/withdraw): ДВЕ строки (двойная запись).
      – сторона игрока: operation_id=op_id, uid=<игрок>, item=NULL, ref=<gid>,
        gold_delta = −amount (вклад) / +amount (снятие);
      – сторона банка:  operation_id=op_id+':bank', uid=0 (сток-адрес банка, как
        uid=0 у комиссии аукциона), item=NULL, ref=<gid>, counterparty=<игрок>,
        gold_delta = +amount / −amount.
    Сумма дельт пары = 0 (двойная запись). bank_gold реконструируется как
    SUM(gold_delta) WHERE uid=0 AND ref=<gid> (см. bank_gold_from_ledger).
  • ПРЕДМЕТ (deposit/withdraw): gold_delta=0 → на баланс золота не влияет, вторая
    строка не нужна. Одна строка: uid=<игрок>, item=<ключ предмета>, ref=<gid>,
    operation='guild_dep_item'/'guild_wd_item'.
  • СОЗДАНИЕ: одна строка (сток) — стоимость СГОРАЕТ, а не оседает в казне:
    uid=<лидер>, operation='guild_create', gold_delta=−cost, item=NULL, ref=<gid>.

Права рангов — rank_can (чистая функция, зеркалит engine/guild.py): лидер может
всё; снимать из банка (withdraw) — до офицера включительно; членство/вклад —
любой валидный ранг.
"""
import json
import time

# Переиспользуем ядро экономики (Этап 3.1): детектор дубликата PK, разбор JSONB,
# блокировку/запись персонажа и быстрый путь идемпотентности. LEDGER_SQL здесь
# СВОЙ — с колонкой ref (у econ_tx её в INSERT нет).
from engine.econ_tx import (
    _is_duplicate, _inv, _lock_char, _op_done, _persist_char,
)

# Строка журнала гильд-операций. Отличается от econ_tx.LEDGER_SQL наличием ref
# (сюда пишем gid) — по нему поднимаются все денежные движения гильдии.
LEDGER_SQL = (
    "INSERT INTO economy_ledger "
    "(operation_id, uid, operation, gold_delta, item, counterparty, created, ref) "
    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8)"
)

# ── Модель рангов (ЗЕРКАЛО engine/guild.py: RANK_ORDER и *_MAX) ──
# Держим локальную копию, чтобы ядро гильдий было самодостаточным и не тянуло
# зависимостей ради проверки права. При правке иерархии — синхронить оба места.
RANK_ORDER = ["leader", "deputy", "senior_officer", "officer", "sergeant", "member"]
_INVITE_MAX = RANK_ORDER.index("sergeant")    # приглашать — до сержанта включительно
_WITHDRAW_MAX = RANK_ORDER.index("officer")   # снимать из банка — до офицера включительно
_ADMIN_MAX = RANK_ORDER.index("deputy")       # управлять составом — лидер/зам
_RIGHT_MAX = {"invite": _INVITE_MAX, "withdraw": _WITHDRAW_MAX, "admin": _ADMIN_MAX}


def rank_can(rank: str, right: str) -> bool:
    """Чистая проверка права по рангу (без БД). Зеркалит engine/guild.py:
    'member'/'deposit' (членство/вклад) — любой валидный ранг; 'invite'/'withdraw'/
    'admin' — позиция ранга в иерархии не ниже порога права. Лидер (индекс 0)
    проходит любой порог → может всё. Неизвестный ранг → False."""
    if rank not in RANK_ORDER:
        return False
    if right in ("member", "deposit"):
        return True                                   # членство/вклад — любой ранг
    if right in _RIGHT_MAX:
        return RANK_ORDER.index(rank) <= _RIGHT_MAX[right]
    return False


# ───────────────────────── хелперы гильдии ─────────────────────────
async def _lock_guild(conn, gid):
    """Заблокировать строку гильдии (FOR UPDATE) и вернуть изменяемый снимок
    {'gid','name','leader','bank_gold','bank_items'} или None, если гильдии нет."""
    row = await conn.fetchrow(
        "SELECT gid, name, leader, bank_gold, bank_items "
        "FROM guilds WHERE gid=$1 FOR UPDATE", str(gid))
    if row is None:
        return None
    return {"gid": str(row["gid"]), "name": row["name"], "leader": int(row["leader"]),
            "bank_gold": int(row["bank_gold"]), "bank_items": _inv(row["bank_items"])}


async def _persist_guild_bank(conn, gid, g):
    """Записать банк гильдии обратно (bank_items → JSONB-строкой)."""
    await conn.execute(
        "UPDATE guilds SET bank_gold=$1, bank_items=$2 WHERE gid=$3",
        int(g["bank_gold"]), json.dumps(g["bank_items"]), str(gid))


async def _member_rank(conn, uid, gid):
    """Ранг персонажа в этой гильдии из guild_members или None, если он не в ней.
    guild_members.uid — PRIMARY KEY: WHERE uid=$1 AND gid=$2 вернёт строку только
    если игрок действительно состоит именно в этой гильдии."""
    row = await conn.fetchrow(
        "SELECT rank FROM guild_members WHERE uid=$1 AND gid=$2", int(uid), str(gid))
    return row["rank"] if row else None


# ───────────────────────── вклад золота ─────────────────────────
async def deposit_gold(cf, uid, gid, amount, op_id):
    """Внести золото персонажа в казну гильдии одной транзакцией.

    Порядок: идемпотентность → FOR UPDATE characters(uid) → FOR UPDATE guilds(gid)
    (детерминированный порядок персонаж→гильдия) → членство → проверки (amount>0,
    хватает золота) → char.gold−amount, bank_gold+amount → двойная запись ledger
    (игрок −amount / банк uid=0 +amount, обе ref=gid; сумма = 0) → COMMIT.

    → (ok: bool, msg: str, char_gold | None, bank_gold | None). Значения не-None
    отдаются ТОЛЬКО при реальном применении — обновить память ПОСЛЕ коммита;
    идемпотентный повтор/ошибка → (…, None, None), память не трогаем.
    """
    amount = int(amount)
    async with cf() as conn:
        if await _op_done(conn, op_id):
            return True, "Уже зачислено в казну.", None, None
        now = time.time()
        try:
            async with conn.transaction():
                char = await _lock_char(conn, uid)          # 1) персонаж
                guild = await _lock_guild(conn, gid)        # 2) гильдия
                rank = await _member_rank(conn, uid, gid)
                if char is None:
                    result = (False, "Персонаж не найден.", None, None)
                elif guild is None:
                    result = (False, "Гильдия не найдена.", None, None)
                elif rank is None:
                    result = (False, "Вы не состоите в этой гильдии.", None, None)
                elif amount <= 0:
                    result = (False, "Сумма должна быть положительной.", None, None)
                elif char["gold"] < amount:
                    result = (False, "Недостаточно монет.", None, None)
                else:
                    char["gold"] -= amount
                    guild["bank_gold"] += amount
                    await _persist_char(conn, uid, char)
                    await _persist_guild_bank(conn, gid, guild)
                    # двойная запись: сторона игрока (−) и сторона банка (uid=0, +)
                    await conn.execute(LEDGER_SQL, op_id, int(uid),
                                       "guild_dep_gold", -amount, None, None, now, str(gid))
                    await conn.execute(LEDGER_SQL, op_id + ":bank", 0,
                                       "guild_dep_gold", amount, None, int(uid), now, str(gid))
                    result = (True, "Внесено в казну.", char["gold"], guild["bank_gold"])
        except Exception as exc:
            if _is_duplicate(exc):
                return True, "Уже зачислено в казну.", None, None
            return False, "⚙️ Банк гильдии временно недоступен.", None, None
        return result


# ───────────────────────── снятие золота ─────────────────────────
async def withdraw_gold(cf, uid, gid, amount, op_id):
    """Снять золото из казны на персонажа одной транзакцией.

    То же, что deposit_gold, но в обратную сторону и с проверкой ПРАВА: ранг из
    guild_members внутри транзакции должен уметь снимать (rank_can(rank,'withdraw');
    лидер может всё). Проверка казны — bank_gold >= amount. Две строки ledger
    (игрок +amount / банк uid=0 −amount, обе ref=gid; сумма = 0).

    → (ok, msg, char_gold | None, bank_gold | None).
    """
    amount = int(amount)
    async with cf() as conn:
        if await _op_done(conn, op_id):
            return True, "Уже снято из казны.", None, None
        now = time.time()
        try:
            async with conn.transaction():
                char = await _lock_char(conn, uid)          # 1) персонаж
                guild = await _lock_guild(conn, gid)        # 2) гильдия
                rank = await _member_rank(conn, uid, gid)
                if char is None:
                    result = (False, "Персонаж не найден.", None, None)
                elif guild is None:
                    result = (False, "Гильдия не найдена.", None, None)
                elif rank is None:
                    result = (False, "Вы не состоите в этой гильдии.", None, None)
                elif not rank_can(rank, "withdraw"):
                    result = (False, "Недостаточно прав для снятия.", None, None)
                elif amount <= 0:
                    result = (False, "Сумма должна быть положительной.", None, None)
                elif guild["bank_gold"] < amount:
                    result = (False, "В казне недостаточно монет.", None, None)
                else:
                    char["gold"] += amount
                    guild["bank_gold"] -= amount
                    await _persist_char(conn, uid, char)
                    await _persist_guild_bank(conn, gid, guild)
                    # двойная запись: игрок (+), банк (uid=0, −)
                    await conn.execute(LEDGER_SQL, op_id, int(uid),
                                       "guild_wd_gold", amount, None, None, now, str(gid))
                    await conn.execute(LEDGER_SQL, op_id + ":bank", 0,
                                       "guild_wd_gold", -amount, None, int(uid), now, str(gid))
                    result = (True, "Снято из казны.", char["gold"], guild["bank_gold"])
        except Exception as exc:
            if _is_duplicate(exc):
                return True, "Уже снято из казны.", None, None
            return False, "⚙️ Банк гильдии временно недоступен.", None, None
        return result


# ───────────────────────── вклад предмета ─────────────────────────
async def deposit_item(cf, uid, gid, item, op_id):
    """Сдать один экземпляр предмета из сумки персонажа на склад гильдии.

    Порядок: идемпотентность → блокировки персонаж→гильдия → членство → предмет
    есть в сумке → снять один экземпляр из inventory, добавить в bank_items →
    ledger (gold_delta=0, item=<предмет>, ref=<gid>) → COMMIT.

    → (ok, msg, char_inventory | None, bank_items | None).
    """
    async with cf() as conn:
        if await _op_done(conn, op_id):
            return True, "Предмет уже на складе.", None, None
        now = time.time()
        try:
            async with conn.transaction():
                char = await _lock_char(conn, uid)          # 1) персонаж
                guild = await _lock_guild(conn, gid)        # 2) гильдия
                rank = await _member_rank(conn, uid, gid)
                if char is None:
                    result = (False, "Персонаж не найден.", None, None)
                elif guild is None:
                    result = (False, "Гильдия не найдена.", None, None)
                elif rank is None:
                    result = (False, "Вы не состоите в этой гильдии.", None, None)
                elif item not in char["inventory"]:
                    result = (False, "Предмета нет в сумке.", None, None)
                else:
                    char["inventory"].remove(item)          # один экземпляр
                    guild["bank_items"].append(item)
                    await _persist_char(conn, uid, char)
                    await _persist_guild_bank(conn, gid, guild)
                    await conn.execute(LEDGER_SQL, op_id, int(uid),
                                       "guild_dep_item", 0, item, None, now, str(gid))
                    result = (True, "Сдано на склад.",
                              char["inventory"], guild["bank_items"])
        except Exception as exc:
            if _is_duplicate(exc):
                return True, "Предмет уже на складе.", None, None
            return False, "⚙️ Банк гильдии временно недоступен.", None, None
        return result


# ───────────────────────── снятие предмета ─────────────────────────
async def withdraw_item(cf, uid, gid, item, op_id):
    """Забрать один экземпляр предмета со склада гильдии в сумку персонажа.

    Как deposit_item, но с проверкой ПРАВА (rank_can(rank,'withdraw')) и предмет
    должен быть на складе. Возврат — обновлённые inventory/bank_items.

    → (ok, msg, char_inventory | None, bank_items | None).
    """
    async with cf() as conn:
        if await _op_done(conn, op_id):
            return True, "Предмет уже забран.", None, None
        now = time.time()
        try:
            async with conn.transaction():
                char = await _lock_char(conn, uid)          # 1) персонаж
                guild = await _lock_guild(conn, gid)        # 2) гильдия
                rank = await _member_rank(conn, uid, gid)
                if char is None:
                    result = (False, "Персонаж не найден.", None, None)
                elif guild is None:
                    result = (False, "Гильдия не найдена.", None, None)
                elif rank is None:
                    result = (False, "Вы не состоите в этой гильдии.", None, None)
                elif not rank_can(rank, "withdraw"):
                    result = (False, "Недостаточно прав, чтобы забрать со склада.", None, None)
                elif item not in guild["bank_items"]:
                    result = (False, "Такого предмета нет на складе.", None, None)
                else:
                    guild["bank_items"].remove(item)        # один экземпляр
                    char["inventory"].append(item)
                    await _persist_char(conn, uid, char)
                    await _persist_guild_bank(conn, gid, guild)
                    await conn.execute(LEDGER_SQL, op_id, int(uid),
                                       "guild_wd_item", 0, item, None, now, str(gid))
                    result = (True, "Забрано со склада.",
                              char["inventory"], guild["bank_items"])
        except Exception as exc:
            if _is_duplicate(exc):
                return True, "Предмет уже забран.", None, None
            return False, "⚙️ Банк гильдии временно недоступен.", None, None
        return result


# ───────────────────────── создание гильдии ─────────────────────────
async def create_guild(cf, gid, name, leader_uid, cost, op_id):
    """Основать гильдию одной транзакцией: списать cost с лидера, создать строку
    guilds и запись guild_members(лидер, rank='leader'), записать ledger-сток.

    Порядок: идемпотентность → FOR UPDATE characters(лидер) → gid ещё не занят →
    золота хватает → gold−cost → INSERT guilds + guild_members → ledger
    ('guild_create', −cost, item=NULL, ref=gid; одна строка — cost сгорает) → COMMIT.

    → (ok, msg, leader_gold | None). Стоимость — сток (не оседает в казне).
    """
    cost = int(cost)
    async with cf() as conn:
        if await _op_done(conn, op_id):
            return True, "Гильдия уже основана.", None
        now = time.time()
        try:
            async with conn.transaction():
                leader = await _lock_char(conn, leader_uid)
                exists = await conn.fetchrow(
                    "SELECT gid FROM guilds WHERE gid=$1", str(gid))
                if leader is None:
                    result = (False, "Персонаж не найден.", None)
                elif exists is not None:
                    result = (False, "Гильдия с таким идентификатором уже есть.", None)
                elif leader["gold"] < cost:
                    result = (False, "Недостаточно монет на основание.", None)
                else:
                    leader["gold"] -= cost
                    await _persist_char(conn, leader_uid, leader)
                    await conn.execute(
                        "INSERT INTO guilds (gid, name, leader, bank_gold, bank_items, created) "
                        "VALUES ($1,$2,$3,0,'[]',$4)",
                        str(gid), name[:24], int(leader_uid), now)
                    await conn.execute(
                        "INSERT INTO guild_members (uid, gid, rank, joined) "
                        "VALUES ($1,$2,'leader',$3)",
                        int(leader_uid), str(gid), now)
                    await conn.execute(LEDGER_SQL, op_id, int(leader_uid),
                                       "guild_create", -cost, None, None, now, str(gid))
                    result = (True, "Гильдия основана.", leader["gold"])
        except Exception as exc:
            if _is_duplicate(exc):
                return True, "Гильдия уже основана.", None
            return False, "⚙️ Банк гильдии временно недоступен.", None
        return result


# ───────────────────────── чтение / миграция / сверка ─────────────────────────
async def load_guilds(cf):
    """Все гильдии и их состав из БД в формате GuildManager.guilds:
    {gid: {name, leader, members:[uid], ranks:{str(uid):rank}, bank_gold,
    bank_items, founded}} — готово для подстановки в память при старте (когда
    таблица guilds непуста, источником истины становится БД, а guilds.json
    игнорируется)."""
    async with cf() as conn:
        grows = await conn.fetch(
            "SELECT gid, name, leader, bank_gold, bank_items, created FROM guilds")
        mrows = await conn.fetch(
            "SELECT uid, gid, rank, joined FROM guild_members")
    guilds = {}
    for r in grows:
        guilds[str(r["gid"])] = {
            "name": r["name"], "leader": int(r["leader"]),
            "members": [], "ranks": {},
            "bank_gold": int(r["bank_gold"]), "bank_items": _inv(r["bank_items"]),
            "founded": int(r["created"]) if r["created"] is not None else 0,
        }
    for r in mrows:
        g = guilds.get(str(r["gid"]))
        if g is None:
            continue
        uid = int(r["uid"])
        g["members"].append(uid)
        g["ranks"][str(uid)] = r["rank"]
    return guilds


async def import_from_manager(cf, dump):
    """Одноразовая миграция из формата guilds.json (GuildManager.save):
    dump = {'guilds': {gid: {name, leader, members, ranks, bank_gold, bank_items,
    founded}}, 'next': N}. Идемпотентна (ON CONFLICT DO NOTHING по PK gid и по PK
    uid guild_members). Возвращает число РЕАЛЬНО вставленных гильдий — при
    повторном вызове вернёт 0 (набор в БД не задвоится). Переживёт и «голый»
    словарь {gid: {...}} (без обёртки 'guilds') — на случай прямой передачи
    guild_mgr.guilds."""
    guilds = (dump or {}).get("guilds") if isinstance(dump, dict) and "guilds" in dump else dump
    guilds = guilds or {}
    n = 0
    async with cf() as conn:
        async with conn.transaction():
            for gid, g in guilds.items():
                founded = float(g.get("founded") or time.time())
                tag = await conn.execute(
                    "INSERT INTO guilds (gid, name, leader, bank_gold, bank_items, created) "
                    "VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (gid) DO NOTHING",
                    str(gid), g.get("name", ""), int(g.get("leader", 0)),
                    int(g.get("bank_gold", 0)), json.dumps(list(g.get("bank_items", []))),
                    founded)
                if isinstance(tag, str) and tag.rstrip().endswith(" 1"):
                    n += 1                       # гильдия реально вставлена (не конфликт)
                ranks = g.get("ranks", {}) or {}
                for uid in g.get("members", []):
                    await conn.execute(
                        "INSERT INTO guild_members (uid, gid, rank, joined) "
                        "VALUES ($1,$2,$3,$4) ON CONFLICT (uid) DO NOTHING",
                        int(uid), str(gid), ranks.get(str(uid), "member"), founded)
    return n


async def ledger_total(cf, uid):
    """Сумма gold_delta по uid (для тестов согласованности двойной записи)."""
    async with cf() as conn:
        row = await conn.fetchrow(
            "SELECT COALESCE(SUM(gold_delta),0) AS total "
            "FROM economy_ledger WHERE uid=$1", int(uid))
    return int(row["total"]) if row and row["total"] is not None else 0


async def ledger_ref_total(cf, ref):
    """Сумма gold_delta по ref (=gid) — расследуемость: денежный баланс вкладов и
    снятий гильдии = 0 (двойная запись), а основание — сток (−cost)."""
    async with cf() as conn:
        row = await conn.fetchrow(
            "SELECT COALESCE(SUM(gold_delta),0) AS total "
            "FROM economy_ledger WHERE ref=$1", str(ref))
    return int(row["total"]) if row and row["total"] is not None else 0


async def bank_gold_from_ledger(cf, gid):
    """Реконструкция bank_gold из журнала: сумма банк-стороны (uid=0) по ref=gid.
    Для тестов согласованности двойной записи — должна равняться guilds.bank_gold."""
    async with cf() as conn:
        row = await conn.fetchrow(
            "SELECT COALESCE(SUM(gold_delta),0) AS total "
            "FROM economy_ledger WHERE uid=0 AND ref=$1", str(gid))
    return int(row["total"]) if row and row["total"] is not None else 0


# ───────────── ростер: персистентность состава (Этап 3.3) ─────────────
# Дыра из отчёта 3.2: guild_tx-банк (выше) проверяет членство/ранг ПО
# guild_members (БД), но вступление/выход/кик/повышение живут ТОЛЬКО в
# GuildManager (память + guilds.json) — состав в guild_members попадал лишь
# через create_guild (лидер) и import_from_manager (одноразовая миграция при
# старте). Игрок, вступивший ПОСЛЕ миграции, получал от банка «Вы не состоите
# в этой гильдии», а рестарт (load_guilds читает БД) откатывал состав к
# моменту миграции — рантайм-изменения не персистились.
#
# Хелперы ниже — тонкое зеркало guild_mgr → guild_members. GuildManager
# ОСТАЁТСЯ единственным источником бизнес-правил (лимиты приглашений,
# иерархия прав на кик/повышение и т.п.) — ни один из хелперов права не
# проверяет и решения не принимает, только повторяет в БД то, что guild_mgr
# уже решил. Вызывающая сторона (bot/main.py) обязана дёргать их ПОСЛЕ того,
# как guild_mgr подтвердил действие — не раньше и не вместо него.
async def add_member(cf, uid, gid, rank='member'):
    """Записать персонажа в состав гильдии (вступление). guild_members.uid —
    PRIMARY KEY (игрок состоит максимум в одной гильдии), поэтому ON CONFLICT
    (uid) DO UPDATE: перевступление (в т.ч. в ДРУГУЮ гильдию — после выхода
    или кика из старой) перезаписывает старую строку целиком (gid/rank/joined),
    а не падает дубликатом ключа. Ledger не пишем — смена состава не денежная
    операция, двойная запись здесь не нужна.

    → (ok, msg)."""
    now = time.time()
    async with cf() as conn:
        try:
            await conn.execute(
                "INSERT INTO guild_members (uid, gid, rank, joined) VALUES ($1,$2,$3,$4) "
                "ON CONFLICT (uid) DO UPDATE SET "
                "gid=EXCLUDED.gid, rank=EXCLUDED.rank, joined=EXCLUDED.joined",
                int(uid), str(gid), rank, now)
        except Exception:
            return False, "⚙️ Не удалось обновить состав гильдии."
    return True, "Состав гильдии обновлён."


async def remove_member(cf, uid, gid):
    """Убрать персонажа из состава ИМЕННО этой гильдии (выход/кик). WHERE по
    uid И gid — если персонаж уже успел перевступить в другую гильдию (гонка
    или повторный клик), чужую новую связь не заденет. Пустую гильдию сама не
    удаляет — роспуск (см. delete_guild) отдельным вызовом.

    → (ok, msg)."""
    async with cf() as conn:
        try:
            await conn.execute(
                "DELETE FROM guild_members WHERE uid=$1 AND gid=$2", int(uid), str(gid))
        except Exception:
            return False, "⚙️ Не удалось обновить состав гильдии."
    return True, "Состав гильдии обновлён."


async def set_rank(cf, uid, gid, rank):
    """Изменить ранг персонажа (повышение/понижение) внутри ЕГО гильдии. WHERE
    по uid И gid: если персонаж не числится в guild_members именно в этой
    гильдии (напр. вступил до фикса 3.3, а ensure_member ещё не догнал) —
    UPDATE не находит строку → явный отказ, а не тихое создание записи мимо
    add_member.

    → (ok, msg)."""
    async with cf() as conn:
        try:
            tag = await conn.execute(
                "UPDATE guild_members SET rank=$1 WHERE uid=$2 AND gid=$3",
                rank, int(uid), str(gid))
        except Exception:
            return False, "⚙️ Не удалось обновить ранг."
    if isinstance(tag, str) and tag.rstrip().endswith(" 0"):
        return False, "Игрок не состоит в этой гильдии."
    return True, "Ранг обновлён."


async def ensure_member(cf, uid, gid, rank='member'):
    """Страховочный апсерт членства — догоняющая синхронизация БД↔память
    (Этап 3.3) для игроков, вступивших/повышенных ДО этого фикса: guild_mgr
    уже считает их членами нужного ранга, а guild_members о них ещё не знает,
    из-за чего guild_tx-банк (проверяет ИМЕННО guild_members) отвечал «Вы не
    состоите в этой гильдии». Технически идентична add_member (тот же INSERT
    ON CONFLICT DO UPDATE) — но по смыслу ДРУГАЯ операция: не «вступить», а
    «подтвердить то, что guild_mgr уже считает истиной».

    ВАЖНО: сама по себе НИКОГО «членом» не делает и прав не проверяет —
    вызывать ТОЛЬКО когда guild_mgr.guild_of(uid)/rank(uid) уже подтвердили
    членство именно в этой gid (см. вызовы перед 4 банк-операциями в
    bot/main.py). Вызов вне этого контракта был бы обходом guild_mgr, а не
    синхронизацией с ним.

    → ok (bool). Сбой БД → False; сама банковская транзакция дальше всё равно
    корректно откажет по _member_rank, если синхронизация не прошла —
    ensure_member влияет на UX (меньше ложных отказов), не на корректность."""
    ok, _msg = await add_member(cf, uid, gid, rank)
    return ok


async def delete_guild(cf, gid):
    """Удалить гильдию и весь её состав (роспуск). На Этап 3.3 в bot/main.py
    НЕТ отдельного колбэка роспуска — авто-роспуск при уходе последнего
    участника живёт только внутри GuildManager.leave (память/guilds.json), без
    зеркала в guild_members. Хелпер заведён заранее по ТЗ: когда/если явный
    колбэк роспуска появится, его нужно будет подключить сюда одним вызовом;
    пока НЕ вызывается из bot/main.py.

    Порядок: сначала guild_members (по gid), потом guilds — в одной
    транзакции, чтобы либо распустилось всё, либо ничего.

    → (ok, msg)."""
    async with cf() as conn:
        try:
            async with conn.transaction():
                await conn.execute("DELETE FROM guild_members WHERE gid=$1", str(gid))
                await conn.execute("DELETE FROM guilds WHERE gid=$1", str(gid))
        except Exception:
            return False, "⚙️ Не удалось удалить гильдию."
    return True, "Гильдия распущена."
