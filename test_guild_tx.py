# -*- coding: utf-8 -*-
"""
Тесты транзакционного ядра гильд-банка (engine/guild_tx.py) — БЕЗ Postgres.

Мок-СУБД (FakeConn/FakePool) ОСОЗНАННО продублирован из test_econ_tx.py и научен
новым SQL-подстрокам guild_tx (таблицы guilds/guild_members, колонка ledger.ref,
8-колоночный INSERT в economy_ledger). Дублируем намеренно: сьюты независимы —
каждый тест-файл прогоняется отдельным процессом (run_tests.py), и связывать их
общим модулем-хелпером значит плодить хрупкую связанность ради экономии ~80 строк.
Таблицы — обычные dict-ы; execute/fetchrow/fetch понимают ТОЛЬКО те SQL-строки,
что реально шлёт guild_tx (и переиспользуемые им хелперы econ_tx). transaction() —
контекст-менеджер: снимок на входе, откат к снимку при исключении/сбое коммита.
Конфликт PRIMARY KEY эмулируется исключением с 'duplicate key'; инъекция сбоя на
конкретном execute — fail_on_execute (одноразовый), сбой коммита — fail_commit.

Проверяется: атомарность вклада (перс−/банк+), полный откат при сбое между
списанием и зачислением, права снятия (ранг), лимит казны, членство, предметы,
идемпотентность op_id (золото и создание), идемпотентность миграции из JSON,
двойная запись (сумма пары строк = 0, реконструкция bank_gold из журнала) и
чтение состояния «после рестарта» (load_guilds).

Этап 3.3 добавил ростер-хелперы (add_member/remove_member/set_rank/
ensure_member/delete_guild) — тонкое зеркало рантайм-решений GuildManager в
guild_members, закрывающее дыру 3.2: вступление/выход/кик/повышение раньше
жили только в памяти+guilds.json, а банк проверяет членство по БД. Ниже —
перевступление (перезапись по PK), отказ set_rank не-члену, идемпотентность
ensure_member, сквозной сценарий «ростер меняет доступ к банку» и то, что
load_guilds после ростер-операций мимо create_guild/миграции отражает их
(имитация рестарта).
"""
import asyncio
import copy
import json
import sys

from engine import guild_tx


# ─────────────────────────── мок-СУБД ───────────────────────────
class _DupKey(Exception):
    """Имитация нарушения PRIMARY KEY (asyncpg.UniqueViolationError)."""


class _Tx:
    """Контекст транзакции: снимок на входе, откат при исключении/сбое коммита."""
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        self.conn._snapshot = self.conn._snap()
        return self

    async def __aexit__(self, et, ev, tb):
        if et is not None:                 # исключение в теле → откат
            self.conn._restore(self.conn._snapshot)
            return False
        if self.conn.fail_commit:          # сбой на коммите → откат + исключение
            self.conn._restore(self.conn._snapshot)
            raise RuntimeError("commit failed")
        return False                       # успешный коммит


class FakeConn:
    def __init__(self, characters=None, guilds=None, members=None, ledger=None):
        # characters: uid -> {"gold": int, "inventory": [str, ...]}
        self.characters = characters or {}
        # guilds: gid(str) -> {gid, name, leader, bank_gold, bank_items:[..], created}
        self.guilds = guilds or {}
        # members: uid -> {uid, gid, rank, joined}
        self.members = members or {}
        # ledger: operation_id -> {uid, operation, gold_delta, item, counterparty, created, ref}
        self.ledger = ledger or {}
        self.fail_on_execute = None        # подстрока SQL → одноразовый сбой
        self.fail_commit = False           # сбой на коммите (см. _Tx)
        self._snapshot = None

    # — снимок/откат для транзакции —
    def _snap(self):
        return (copy.deepcopy(self.characters), copy.deepcopy(self.guilds),
                copy.deepcopy(self.members), copy.deepcopy(self.ledger))

    def _restore(self, snap):
        self.characters, self.guilds, self.members, self.ledger = (
            copy.deepcopy(snap[0]), copy.deepcopy(snap[1]),
            copy.deepcopy(snap[2]), copy.deepcopy(snap[3]))

    def transaction(self):
        return _Tx(self)

    # — исполнение SQL (сопоставление по подстрокам) —
    async def execute(self, sql, *args):
        if self.fail_on_execute and self.fail_on_execute in sql:
            self.fail_on_execute = None    # одноразовый сбой
            raise RuntimeError("injected failure")

        if "economy_ledger" in sql:                       # INSERT ledger (8 колонок)
            op_id, uid, operation, gold_delta, item, cp, created, ref = args
            if op_id in self.ledger:
                raise _DupKey("duplicate key value violates unique constraint "
                              "\"economy_ledger_pkey\"")
            self.ledger[op_id] = {"uid": int(uid), "operation": operation,
                                  "gold_delta": int(gold_delta), "item": item,
                                  "counterparty": cp, "created": created, "ref": ref}
            return "INSERT 0 1"

        if "UPDATE characters" in sql:                    # UPDATE gold/inventory
            gold, inv_json, uid = args
            self.characters[int(uid)] = {
                "gold": int(gold), "inventory": json.loads(inv_json)}
            return "UPDATE 1"

        if "UPDATE guilds" in sql:                        # UPDATE банка гильдии
            bank_gold, bank_items_json, gid = args
            g = self.guilds[str(gid)]
            g["bank_gold"] = int(bank_gold)
            g["bank_items"] = json.loads(bank_items_json)
            return "UPDATE 1"

        if "INSERT INTO guilds" in sql:                   # создание/миграция гильдии
            if "ON CONFLICT" in sql:                      # миграция (6 арг)
                gid, name, leader, bank_gold, bank_items_json, created = args
                if str(gid) in self.guilds:
                    return "INSERT 0 0"                    # конфликт: пропустить
                self.guilds[str(gid)] = {
                    "gid": str(gid), "name": name, "leader": int(leader),
                    "bank_gold": int(bank_gold),
                    "bank_items": json.loads(bank_items_json), "created": created}
                return "INSERT 0 1"
            gid, name, leader, created = args             # create_guild (4 арг, bank=0/'[]')
            if str(gid) in self.guilds:
                raise _DupKey("duplicate key ... \"guilds_pkey\"")
            self.guilds[str(gid)] = {
                "gid": str(gid), "name": name, "leader": int(leader),
                "bank_gold": 0, "bank_items": [], "created": created}
            return "INSERT 0 1"

        if "INSERT INTO guild_members" in sql:            # член/лидер/ростер
            if "DO UPDATE" in sql:                        # add_member/ensure_member (апсерт-перезапись)
                uid, gid, rank, joined = args
                self.members[int(uid)] = {"uid": int(uid), "gid": str(gid),
                                          "rank": rank, "joined": joined}
                return "INSERT 0 1"
            if "ON CONFLICT" in sql:                      # миграция (DO NOTHING, 4 арг)
                uid, gid, rank, joined = args
                if int(uid) in self.members:
                    return "INSERT 0 0"
                self.members[int(uid)] = {"uid": int(uid), "gid": str(gid),
                                          "rank": rank, "joined": joined}
                return "INSERT 0 1"
            uid, gid, joined = args                       # create_guild (3 арг, rank='leader')
            if int(uid) in self.members:
                raise _DupKey("duplicate key ... \"guild_members_pkey\"")
            self.members[int(uid)] = {"uid": int(uid), "gid": str(gid),
                                      "rank": "leader", "joined": joined}
            return "INSERT 0 1"

        if "DELETE FROM guild_members" in sql:            # remove_member / delete_guild
            if len(args) == 2:                            # remove_member: WHERE uid=$1 AND gid=$2
                uid, gid = args
                m = self.members.get(int(uid))
                if m is not None and m["gid"] == str(gid):
                    del self.members[int(uid)]
                    return "DELETE 1"
                return "DELETE 0"
            gid = str(args[0])                            # delete_guild: WHERE gid=$1 (весь состав)
            to_del = [u for u, m in self.members.items() if m["gid"] == gid]
            for u in to_del:
                del self.members[u]
            return f"DELETE {len(to_del)}"

        if "UPDATE guild_members" in sql:                 # set_rank
            rank, uid, gid = args
            m = self.members.get(int(uid))
            if m is not None and m["gid"] == str(gid):
                m["rank"] = rank
                return "UPDATE 1"
            return "UPDATE 0"

        if "DELETE FROM guilds" in sql:                   # delete_guild: строка гильдии
            gid = str(args[0])
            existed = gid in self.guilds
            self.guilds.pop(gid, None)
            return "DELETE 1" if existed else "DELETE 0"

        raise AssertionError("FakeConn.execute: неизвестный SQL:\n" + sql)

    async def fetchrow(self, sql, *args):
        if "COALESCE(SUM" in sql:                         # суммы журнала
            if "uid=0 AND ref" in sql:                    # bank_gold_from_ledger
                ref = str(args[0])
                total = sum(r["gold_delta"] for r in self.ledger.values()
                            if r["uid"] == 0 and r["ref"] == ref)
            elif "WHERE ref=$1" in sql:                   # ledger_ref_total
                ref = str(args[0])
                total = sum(r["gold_delta"] for r in self.ledger.values()
                            if r["ref"] == ref)
            else:                                         # ledger_total (WHERE uid=$1)
                uid = int(args[0])
                total = sum(r["gold_delta"] for r in self.ledger.values()
                            if r["uid"] == uid)
            return {"total": total}

        if "FROM economy_ledger" in sql:                  # _op_done (WHERE operation_id)
            op_id = args[0]
            return {"one": 1} if op_id in self.ledger else None

        if "FROM characters" in sql:                      # _lock_char
            uid = int(args[0])
            ch = self.characters.get(uid)
            if ch is None:
                return None
            return {"gold": int(ch["gold"]),
                    "inventory": json.dumps(ch["inventory"])}

        if "FROM guilds" in sql:                          # _lock_guild / проверка gid
            gid = str(args[0])
            g = self.guilds.get(gid)
            return dict(g) if g is not None else None

        if "FROM guild_members" in sql:                   # _member_rank (uid AND gid)
            uid = int(args[0])
            gid = str(args[1])
            m = self.members.get(uid)
            return {"rank": m["rank"]} if (m is not None and m["gid"] == gid) else None

        raise AssertionError("FakeConn.fetchrow: неизвестный SQL:\n" + sql)

    async def fetch(self, sql, *args):
        if "FROM guilds" in sql:                          # load_guilds: все гильдии
            return [dict(g) for g in self.guilds.values()]
        if "FROM guild_members" in sql:                   # load_guilds: все члены
            return [dict(m) for m in self.members.values()]
        raise AssertionError("FakeConn.fetch: неизвестный SQL:\n" + sql)


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *a):
        return False


class FakePool:
    """Аналог db.pool: .acquire() → async-контекст с соединением."""
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


def build(characters=None, guilds=None, members=None, ledger=None):
    conn = FakeConn(characters, guilds, members, ledger)
    pool = FakePool(conn)
    return conn, pool.acquire        # (conn для инспекции таблиц, cf для guild_tx)


def seed_guild(conn, gid, name="Гильдия", leader=1, bank_gold=0, bank_items=None, created=1.0):
    conn.guilds[str(gid)] = {"gid": str(gid), "name": name, "leader": int(leader),
                             "bank_gold": int(bank_gold),
                             "bank_items": list(bank_items or []), "created": created}


def seed_member(conn, uid, gid, rank):
    conn.members[int(uid)] = {"uid": int(uid), "gid": str(gid), "rank": rank, "joined": 1.0}


# ─────────────────────────── харнесс проверок ───────────────────────────
_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print("  ❌ FAIL:", label)


# ─────────────────────────── сценарии ───────────────────────────
async def scenario_rank_can(_):
    """Чистая функция прав: лидер всё; withdraw до офицера; членство/вклад — любой ранг."""
    check(guild_tx.rank_can("leader", "withdraw") is True, "rank: лидер снимает")
    check(guild_tx.rank_can("leader", "admin") is True, "rank: лидер управляет составом")
    check(guild_tx.rank_can("officer", "withdraw") is True, "rank: офицер снимает")
    check(guild_tx.rank_can("officer", "admin") is False, "rank: офицер не управляет составом")
    check(guild_tx.rank_can("sergeant", "withdraw") is False, "rank: сержант не снимает")
    check(guild_tx.rank_can("sergeant", "invite") is True, "rank: сержант приглашает")
    check(guild_tx.rank_can("member", "deposit") is True, "rank: рядовой вкладывает")
    check(guild_tx.rank_can("member", "member") is True, "rank: рядовой — член")
    check(guild_tx.rank_can("member", "withdraw") is False, "rank: рядовой не снимает")
    check(guild_tx.rank_can("нет_такого", "member") is False, "rank: неизвестный ранг — прав нет")


async def scenario_deposit_atomic(_):
    """Атомарный вклад золота: перс−, банк+, двойная запись в журнале."""
    conn, cf = build({1: {"gold": 1000, "inventory": []}})
    seed_guild(conn, "1", leader=1, bank_gold=100)
    seed_member(conn, 1, "1", "leader")
    ok, msg, cg, bg = await guild_tx.deposit_gold(cf, 1, "1", 300, "gdep:1:100")
    check(ok is True, "deposit: ok")
    check(cg == 700 and bg == 400, "deposit: возвращены char_gold=700, bank_gold=400")
    check(conn.characters[1]["gold"] == 700, "deposit: в БД у игрока 700")
    check(conn.guilds["1"]["bank_gold"] == 400, "deposit: в БД казна 400")
    row = conn.ledger["gdep:1:100"]
    check(row["operation"] == "guild_dep_gold" and row["gold_delta"] == -300,
          "deposit: строка игрока guild_dep_gold −300")
    check(row["item"] is None and row["ref"] == "1", "deposit: item=NULL, ref=gid")
    bank = conn.ledger["gdep:1:100:bank"]
    check(bank["uid"] == 0 and bank["gold_delta"] == 300 and bank["ref"] == "1",
          "deposit: строка банка uid=0 +300 ref=gid")
    check(row["gold_delta"] + bank["gold_delta"] == 0, "deposit: сумма пары строк = 0")
    check(await guild_tx.bank_gold_from_ledger(cf, "1") == 300,
          "deposit: bank_gold из журнала (uid=0) = +300")


async def scenario_deposit_rollback(_):
    """Сбой МЕЖДУ списанием игрока и зачислением казны → полный откат."""
    conn, cf = build({1: {"gold": 1000, "inventory": []}})
    seed_guild(conn, "1", leader=1, bank_gold=100)
    seed_member(conn, 1, "1", "leader")
    conn.fail_on_execute = "UPDATE guilds"   # падение после UPDATE characters, до ledger
    ok, msg, cg, bg = await guild_tx.deposit_gold(cf, 1, "1", 300, "gdep:1:err")
    check(ok is False and cg is None and bg is None, "rollback: отказ, память не трогаем")
    check(conn.characters[1]["gold"] == 1000, "rollback: списание игрока откатано (1000)")
    check(conn.guilds["1"]["bank_gold"] == 100, "rollback: казна не изменилась (100)")
    check("gdep:1:err" not in conn.ledger and "gdep:1:err:bank" not in conn.ledger,
          "rollback: строки журнала откатаны")
    check(await guild_tx.ledger_ref_total(cf, "1") == 0, "rollback: журнал по гильдии пуст")


async def scenario_commit_failure(_):
    """Сбой на коммите → память не обновляется, журнал пуст."""
    conn, cf = build({1: {"gold": 1000, "inventory": []}})
    seed_guild(conn, "1", leader=1, bank_gold=0)
    seed_member(conn, 1, "1", "leader")
    conn.fail_commit = True
    ok, msg, cg, bg = await guild_tx.deposit_gold(cf, 1, "1", 500, "gdep:commit")
    check(ok is False and cg is None, "commit-fail: отказ")
    check(conn.characters[1]["gold"] == 1000, "commit-fail: золото игрока на месте")
    check(conn.guilds["1"]["bank_gold"] == 0, "commit-fail: казна пуста")
    check("gdep:commit" not in conn.ledger, "commit-fail: журнал пуст (откат)")


async def scenario_withdraw_no_right(_):
    """Снятие рядовым (без права withdraw) → отказ, ничего не изменилось."""
    conn, cf = build({2: {"gold": 0, "inventory": []}})
    seed_guild(conn, "1", leader=1, bank_gold=1000)
    seed_member(conn, 2, "1", "member")      # рядовой — без права снятия
    ok, msg, cg, bg = await guild_tx.withdraw_gold(cf, 2, "1", 100, "gwd:2:1")
    check(ok is False, "no-right: отказ")
    check("прав" in msg.lower(), "no-right: сообщение про права")
    check(conn.characters[2]["gold"] == 0, "no-right: золото игрока не тронуто")
    check(conn.guilds["1"]["bank_gold"] == 1000, "no-right: казна не тронута")
    check("gwd:2:1" not in conn.ledger, "no-right: журнал пуст")


async def scenario_withdraw_over_bank(_):
    """Снятие больше казны → отказ; снятие в пределах казны офицером → успех."""
    conn, cf = build({3: {"gold": 0, "inventory": []}})
    seed_guild(conn, "1", leader=1, bank_gold=50)
    seed_member(conn, 3, "1", "officer")     # офицер — право снятия есть
    ok, msg, cg, bg = await guild_tx.withdraw_gold(cf, 3, "1", 100, "gwd:3:1")
    check(ok is False, "over-bank: отказ (100 > 50)")
    check(conn.guilds["1"]["bank_gold"] == 50, "over-bank: казна не изменилась")
    check(conn.characters[3]["gold"] == 0, "over-bank: золото игрока не изменилось")
    check("gwd:3:1" not in conn.ledger, "over-bank: журнал пуст")
    ok2, msg2, cg2, bg2 = await guild_tx.withdraw_gold(cf, 3, "1", 40, "gwd:3:2")
    check(ok2 is True and cg2 == 40 and bg2 == 10, "over-bank: снятие 40 офицером ок (казна 10)")
    r = conn.ledger["gwd:3:2"]
    b = conn.ledger["gwd:3:2:bank"]
    check(r["gold_delta"] == 40 and b["gold_delta"] == -40 and r["gold_delta"] + b["gold_delta"] == 0,
          "over-bank: двойная запись снятия (сумма = 0)")


async def scenario_non_member(_):
    """Вклад не-членом (нет строки в guild_members) → отказ."""
    conn, cf = build({9: {"gold": 1000, "inventory": []}})
    seed_guild(conn, "1", leader=1, bank_gold=0)   # uid 9 НЕ в guild_members
    ok, msg, cg, bg = await guild_tx.deposit_gold(cf, 9, "1", 100, "gdep:9:1")
    check(ok is False, "non-member: отказ вклада")
    check("состоите" in msg.lower(), "non-member: сообщение про членство")
    check(conn.characters[9]["gold"] == 1000, "non-member: золото не тронуто")
    check(conn.guilds["1"]["bank_gold"] == 0, "non-member: казна не тронута")
    check("gdep:9:1" not in conn.ledger, "non-member: журнал пуст")


async def scenario_items(_):
    """Вклад/снятие предмета + «предмета нет» и «нет права» → отказ."""
    conn, cf = build({1: {"gold": 0, "inventory": ["sword", "gem"]},
                      2: {"gold": 0, "inventory": []}})
    seed_guild(conn, "1", leader=1, bank_items=["shield"])
    seed_member(conn, 1, "1", "leader")
    seed_member(conn, 2, "1", "member")
    # вклад предмета лидером
    ok, msg, inv, bank = await guild_tx.deposit_item(cf, 1, "1", "sword", "gdi:1:sword")
    check(ok is True, "item-dep: ok")
    check("sword" not in conn.characters[1]["inventory"], "item-dep: предмет ушёл из сумки")
    check(conn.guilds["1"]["bank_items"].count("sword") == 1, "item-dep: предмет на складе")
    ld = conn.ledger["gdi:1:sword"]
    check(ld["operation"] == "guild_dep_item" and ld["item"] == "sword"
          and ld["ref"] == "1" and ld["gold_delta"] == 0,
          "item-dep: ledger guild_dep_item, item=предмет, ref=gid, delta 0")
    # «предмета нет» — отказ
    ok2, msg2, inv2, bank2 = await guild_tx.deposit_item(cf, 1, "1", "missing", "gdi:1:miss")
    check(ok2 is False and inv2 is None, "item-dep-missing: отказ (нет в сумке)")
    check("gdi:1:miss" not in conn.ledger, "item-dep-missing: журнал пуст")
    # снятие предмета лидером (право есть)
    ok3, msg3, inv3, bank3 = await guild_tx.withdraw_item(cf, 1, "1", "shield", "gwi:1:shield")
    check(ok3 is True, "item-wd: ok")
    check("shield" in conn.characters[1]["inventory"], "item-wd: предмет в сумке")
    check("shield" not in conn.guilds["1"]["bank_items"], "item-wd: предмет ушёл со склада")
    check(conn.ledger["gwi:1:shield"]["operation"] == "guild_wd_item", "item-wd: ledger guild_wd_item")
    # снятие рядовым (нет права) — отказ
    ok4, msg4, i4, b4 = await guild_tx.withdraw_item(cf, 2, "1", "sword", "gwi:2:sword")
    check(ok4 is False and "прав" in msg4.lower(), "item-wd-noright: отказ рядовому")
    check(conn.guilds["1"]["bank_items"].count("sword") == 1, "item-wd-noright: предмет остался на складе")
    # снятие отсутствующего на складе — отказ (лидером)
    ok5, msg5, i5, b5 = await guild_tx.withdraw_item(cf, 1, "1", "нетакого", "gwi:1:none")
    check(ok5 is False, "item-wd-missing: отказ (нет на складе)")
    check("gwi:1:none" not in conn.ledger, "item-wd-missing: журнал пуст")


async def scenario_deposit_idempotent(_):
    """Повтор op_id вклада → идемпотентно: балансы не двигаются повторно."""
    conn, cf = build({1: {"gold": 1000, "inventory": []}})
    seed_guild(conn, "1", leader=1, bank_gold=0)
    seed_member(conn, 1, "1", "leader")
    r1 = await guild_tx.deposit_gold(cf, 1, "1", 200, "gdep:dup")
    check(r1[0] is True and r1[2] == 800 and r1[3] == 200, "idem: первый вклад применён")
    r2 = await guild_tx.deposit_gold(cf, 1, "1", 200, "gdep:dup")
    check(r2[0] is True, "idem: повтор — ok (идемпотентно)")
    check(r2[2] is None and r2[3] is None, "idem: данных нет → память не двигаем")
    check(conn.characters[1]["gold"] == 800, "idem: золото не списано повторно")
    check(conn.guilds["1"]["bank_gold"] == 200, "idem: казна не пополнилась повторно")
    check(await guild_tx.ledger_total(cf, 1) == -200, "idem: одна запись −200, не −400")


async def scenario_create_idempotent(_):
    """create_guild списывает cost ровно раз; повтор op_id идемпотентен; gid уникален."""
    conn, cf = build({5: {"gold": 600000, "inventory": []},
                      6: {"gold": 600000, "inventory": []}})
    r1 = await guild_tx.create_guild(cf, "7", "Стражи", 5, 500000, "gcreate:7")
    check(r1[0] is True and r1[2] == 100000, "create: основана, gold 100000")
    check("7" in conn.guilds and conn.guilds["7"]["leader"] == 5, "create: строка guilds есть")
    check(conn.members.get(5) is not None and conn.members[5]["rank"] == "leader"
          and conn.members[5]["gid"] == "7", "create: лидер в guild_members")
    cr = conn.ledger["gcreate:7"]
    check(cr["operation"] == "guild_create" and cr["gold_delta"] == -500000
          and cr["ref"] == "7" and cr["item"] is None,
          "create: ledger сток −cost, ref=gid, item=NULL")
    # повтор того же op_id — идемпотентно, cost не списан второй раз
    r2 = await guild_tx.create_guild(cf, "7", "Стражи", 5, 500000, "gcreate:7")
    check(r2[0] is True and r2[2] is None, "create: повтор op_id идемпотентен")
    check(conn.characters[5]["gold"] == 100000, "create: cost списан ровно раз")
    # тот же gid другим лидером/op_id — отказ (gid занят), золото не тронуто
    r3 = await guild_tx.create_guild(cf, "7", "Другие", 6, 500000, "gcreate:7b")
    check(r3[0] is False, "create: занятый gid — отказ")
    check(conn.characters[6]["gold"] == 600000, "create: золото не списано при занятом gid")
    check(6 not in conn.members, "create: второй лидер в guild_members не добавлен")


async def scenario_create_insufficient(_):
    """Основание без денег → отказ, ничего не создано."""
    conn, cf = build({8: {"gold": 100, "inventory": []}})
    ok, msg, cg = await guild_tx.create_guild(cf, "9", "Бедные", 8, 500000, "gcreate:9")
    check(ok is False, "create-poor: отказ")
    check(conn.characters[8]["gold"] == 100, "create-poor: золото не тронуто")
    check("9" not in conn.guilds, "create-poor: гильдия не создана")
    check(8 not in conn.members, "create-poor: членство не создано")
    check("gcreate:9" not in conn.ledger, "create-poor: журнал пуст")


async def scenario_migration_idempotent(_):
    """Миграция из JSON-структуры идемпотентна: дважды → один набор."""
    conn, cf = build({})
    dump = {"guilds": {
        "1": {"name": "Альфа", "leader": 10, "members": [10, 11],
              "ranks": {"10": "leader", "11": "officer"},
              "bank_gold": 500, "bank_items": ["gem"], "founded": 123},
        "2": {"name": "Бета", "leader": 20, "members": [20],
              "ranks": {"20": "leader"}, "bank_gold": 0, "bank_items": [], "founded": 124},
    }, "next": 3}
    n1 = await guild_tx.import_from_manager(cf, dump)
    check(n1 == 2, "migration: вставлено 2 гильдии")
    check(len(conn.guilds) == 2, "migration: 2 гильдии в БД")
    check(conn.guilds["1"]["bank_gold"] == 500 and conn.guilds["1"]["bank_items"] == ["gem"],
          "migration: банк гильдии 1 перенесён")
    check(conn.members[11]["rank"] == "officer" and conn.members[11]["gid"] == "1",
          "migration: член 11 — офицер гильдии 1")
    n2 = await guild_tx.import_from_manager(cf, dump)   # повторная миграция
    check(n2 == 0, "migration: повтор вставил 0 (идемпотентно)")
    check(len(conn.guilds) == 2, "migration: набор гильдий не задвоился")
    check(len(conn.members) == 3, "migration: члены не задвоились (10, 11, 20)")


async def scenario_restart_load(_):
    """«Рестарт»: load_guilds после операций отдаёт актуальные bank/членов; сверка журнала."""
    conn, cf = build({1: {"gold": 10000, "inventory": ["sword"]},
                      2: {"gold": 5000, "inventory": []}})
    seed_guild(conn, "1", name="Клан", leader=1, bank_gold=0, bank_items=[])
    seed_member(conn, 1, "1", "leader")
    seed_member(conn, 2, "1", "officer")
    await guild_tx.deposit_gold(cf, 1, "1", 3000, "gdep:1:r")
    await guild_tx.deposit_gold(cf, 2, "1", 1000, "gdep:2:r")
    await guild_tx.deposit_item(cf, 1, "1", "sword", "gdi:1:r")
    guilds = await guild_tx.load_guilds(cf)          # чтение состояния «после рестарта»
    check("1" in guilds, "restart: гильдия загружена из БД")
    g = guilds["1"]
    check(g["bank_gold"] == 4000, "restart: bank_gold=4000 (3000+1000)")
    check(g["bank_items"] == ["sword"], "restart: bank_items=[sword]")
    check(sorted(g["members"]) == [1, 2], "restart: члены 1 и 2")
    check(g["ranks"]["1"] == "leader" and g["ranks"]["2"] == "officer",
          "restart: ранги восстановлены")
    check(g["name"] == "Клан", "restart: имя гильдии")
    check(await guild_tx.bank_gold_from_ledger(cf, "1") == 4000,
          "restart: bank_gold реконструирован из журнала = 4000")
    check(await guild_tx.ledger_ref_total(cf, "1") == 0,
          "restart: денежный баланс гильдии в журнале = 0 (двойная запись)")


# ───────── Этап 3.3: ростер-хелперы (персистентность состава) ─────────
async def scenario_add_member(_):
    """add_member: создаёт связь; перевступление в ДРУГУЮ гильдию перезаписывает
    (guild_members.uid — PRIMARY KEY), не дублирует и не падает дубликатом ключа."""
    conn, cf = build({})
    seed_guild(conn, "1", name="Альфа", leader=1)
    seed_guild(conn, "2", name="Бета", leader=2)
    ok, _ = await guild_tx.add_member(cf, 10, "1", "member")
    check(ok is True and conn.members[10]["gid"] == "1", "add_member: связь создана")
    ok2, _ = await guild_tx.add_member(cf, 10, "2", "officer")   # «перевступление»
    check(ok2 is True and conn.members[10]["gid"] == "2"
          and conn.members[10]["rank"] == "officer" and len(conn.members) == 1,
          "add_member: перевступление в другую гильдию перезаписывает, не дублирует")


async def scenario_remove_member(_):
    """remove_member: убирает члена ИМЕННО из указанной гильдии; WHERE по gid
    защищает от удаления чужой (актуальной) связи того же uid."""
    conn, cf = build({})
    seed_member(conn, 5, "1", "member")
    seed_member(conn, 6, "2", "leader")
    ok, _ = await guild_tx.remove_member(cf, 5, "1")
    check(ok is True and 5 not in conn.members, "remove_member: строка удалена")
    ok2, _ = await guild_tx.remove_member(cf, 6, "1")    # uid 6 состоит в "2", не в "1"
    check(ok2 is True and conn.members[6]["gid"] == "2",
          "remove_member: неверный gid чужого члена не удаляет")


async def scenario_set_rank(_):
    """set_rank: меняет ранг члена ЭТОЙ гильдии; не член вовсе или член ДРУГОЙ
    гильдии → отказ, запись мимо add_member не создаётся/не портится."""
    conn, cf = build({})
    seed_member(conn, 7, "1", "member")
    ok, _ = await guild_tx.set_rank(cf, 7, "1", "officer")
    check(ok is True and conn.members[7]["rank"] == "officer", "set_rank: ранг обновлён")
    ok2, msg2 = await guild_tx.set_rank(cf, 99, "1", "officer")   # не член вовсе
    check(ok2 is False and 99 not in conn.members,
          "set_rank: не член — отказ, запись не создана")
    seed_member(conn, 8, "2", "member")
    ok3, _ = await guild_tx.set_rank(cf, 8, "1", "officer")       # член ДРУГОЙ гильдии
    check(ok3 is False and conn.members[8]["rank"] == "member",
          "set_rank: член другой гильдии — отказ, ранг не тронут")


async def scenario_ensure_member(_):
    """ensure_member: апсертит недостающую связь (страховка догоняющей
    синхронизации) и не дублирует при повторном вызове с тем же рангом."""
    conn, cf = build({})
    ok = await guild_tx.ensure_member(cf, 20, "1", "sergeant")
    check(ok is True and conn.members[20]["rank"] == "sergeant",
          "ensure_member: апсертит связь с нужным рангом")
    ok2 = await guild_tx.ensure_member(cf, 20, "1", "sergeant")
    check(ok2 is True and len(conn.members) == 1, "ensure_member: повтор не дублирует")


async def scenario_roster_then_bank(_):
    """Сквозной сценарий 3.3 — ростер и банк согласованы: до add_member банк
    отказывает; add_member открывает вклад, но не снятие; set_rank до офицера
    открывает withdraw; remove_member снова закрывает банк для игрока."""
    conn, cf = build({1: {"gold": 5000, "inventory": []}})
    seed_guild(conn, "1", name="Клан", leader=9, bank_gold=1000)
    ok0, msg0, _, _ = await guild_tx.deposit_gold(cf, 1, "1", 100, "pre:1")
    check(ok0 is False, "roster→bank: до add_member банк отказывает (не член)")
    await guild_tx.add_member(cf, 1, "1", "member")
    ok1, _, _, bg1 = await guild_tx.deposit_gold(cf, 1, "1", 100, "roster:dep")
    okw, _, _, _ = await guild_tx.withdraw_gold(cf, 1, "1", 50, "roster:wd0")
    check(ok1 is True and bg1 == 1100 and okw is False,
          "roster→bank: рядовой после add_member вкладывает, но снимать не может")
    await guild_tx.set_rank(cf, 1, "1", "officer")
    ok3, _, _, bg3 = await guild_tx.withdraw_gold(cf, 1, "1", 50, "roster:wd1")
    check(ok3 is True and bg3 == 1050, "roster→bank: офицер после set_rank снимает")
    await guild_tx.remove_member(cf, 1, "1")
    ok4, msg4, _, _ = await guild_tx.deposit_gold(cf, 1, "1", 10, "roster:dep2")
    check(ok4 is False and "состоите" in msg4.lower(),
          "roster→bank: после remove_member банк снова отказывает")


async def scenario_restart_after_roster(_):
    """«Рестарт» после ростер-операций мимо create_guild/миграции: load_guilds
    отражает add_member/set_rank/remove_member — дыра из отчёта 3.2 закрыта."""
    conn, cf = build({})
    seed_guild(conn, "1", name="Стражи", leader=1, bank_gold=0)
    seed_member(conn, 1, "1", "leader")
    await guild_tx.add_member(cf, 2, "1", "member")     # «вступил после миграции»
    await guild_tx.set_rank(cf, 2, "1", "officer")       # «повышен»
    await guild_tx.add_member(cf, 3, "1", "member")
    await guild_tx.remove_member(cf, 3, "1")             # «вышел»
    guilds = await guild_tx.load_guilds(cf)              # чтение состояния «после рестарта»
    g = guilds["1"]
    check(sorted(g["members"]) == [1, 2] and g["ranks"]["2"] == "officer",
          "restart-roster: состав и повышение пережили «рестарт» (load_guilds)")
    check(3 not in g["members"], "restart-roster: вышедший не воскресает после «рестарта»")


async def scenario_delete_guild(_):
    """delete_guild: удаляет гильдию и ВСЕХ её членов одной транзакцией; чужую
    гильдию и её состав не трогает (пока не подключена ни к одному колбэку —
    в bot/main.py нет явного роспуска, см. docstring engine/guild_tx.py)."""
    conn, cf = build({})
    seed_guild(conn, "1", name="Альфа", leader=1)
    seed_member(conn, 1, "1", "leader")
    seed_member(conn, 2, "1", "officer")
    seed_guild(conn, "2", name="Бета", leader=3)
    seed_member(conn, 3, "2", "leader")
    ok, _ = await guild_tx.delete_guild(cf, "1")
    check(ok is True and "1" not in conn.guilds
          and 1 not in conn.members and 2 not in conn.members,
          "delete_guild: гильдия и её члены удалены")
    check("2" in conn.guilds and 3 in conn.members,
          "delete_guild: чужая гильдия/член не тронуты")


async def run_all():
    for fn in (scenario_rank_can, scenario_deposit_atomic, scenario_deposit_rollback,
               scenario_commit_failure, scenario_withdraw_no_right,
               scenario_withdraw_over_bank, scenario_non_member, scenario_items,
               scenario_deposit_idempotent, scenario_create_idempotent,
               scenario_create_insufficient, scenario_migration_idempotent,
               scenario_restart_load,
               scenario_add_member, scenario_remove_member, scenario_set_rank,
               scenario_ensure_member, scenario_roster_then_bank,
               scenario_restart_after_roster, scenario_delete_guild):
        await fn(None)


def main():
    asyncio.run(run_all())
    print(f"ИТОГО: ✅ {_passed} пройдено, ❌ {_failed} провалено")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
