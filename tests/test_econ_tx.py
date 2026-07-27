# -*- coding: utf-8 -*-
"""
Тесты транзакционного ядра экономики (engine/econ_tx.py) — БЕЗ Postgres.

В файле собственная мок-СУБД (FakeConn/FakePool): таблицы — обычные dict-ы;
execute/fetchrow/fetch понимают ТОЛЬКО те SQL-строки, которые реально шлёт
econ_tx (сопоставление по подстрокам). transaction() — контекст-менеджер:
на входе снимок таблиц, при исключении (или сымитированном сбое коммита) —
откат к снимку. Конфликт PRIMARY KEY economy_ledger эмулируется исключением
с 'duplicate key'. Инъекция сбоя на конкретном execute — fail_on_execute
(одноразовый), сбой коммита — fail_commit.

Проверяется атомарность (откат при обрыве), идемпотентность (повтор op_id),
недоступность проданного лота, прямое зачисление оффлайн-продавцу, комиссия 5%
и двойная запись (сумма дельт).
"""
import asyncio
import copy
import json
import sys

from engine import econ_tx


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
    def __init__(self, characters=None, lots=None, ledger=None):
        # characters: uid -> {"gold": int, "inventory": [str, ...]}
        self.characters = characters or {}
        # lots: lot_id -> {lot_id, seller, item, price, status, created, closed, buyer}
        self.lots = lots or {}
        # ledger: operation_id -> {uid, operation, gold_delta, item, counterparty, created}
        self.ledger = ledger or {}
        self.fail_on_execute = None        # подстрока SQL → одноразовый сбой
        self.fail_commit = False           # сбой на коммите (см. _Tx)
        self._snapshot = None

    # — снимок/откат для транзакции —
    def _snap(self):
        return (copy.deepcopy(self.characters),
                copy.deepcopy(self.lots),
                copy.deepcopy(self.ledger))

    def _restore(self, snap):
        self.characters, self.lots, self.ledger = (
            copy.deepcopy(snap[0]), copy.deepcopy(snap[1]), copy.deepcopy(snap[2]))

    def transaction(self):
        return _Tx(self)

    # — исполнение SQL (сопоставление по подстрокам) —
    async def execute(self, sql, *args):
        if self.fail_on_execute and self.fail_on_execute in sql:
            self.fail_on_execute = None    # одноразовый сбой
            raise RuntimeError("injected failure")

        if "economy_ledger" in sql:                       # INSERT ledger
            op_id, uid, operation, gold_delta, item, cp, created = args
            if op_id in self.ledger:
                raise _DupKey("duplicate key value violates unique constraint "
                              "\"economy_ledger_pkey\"")
            self.ledger[op_id] = {"uid": int(uid), "operation": operation,
                                  "gold_delta": int(gold_delta), "item": item,
                                  "counterparty": cp, "created": created}
            return "INSERT 0 1"

        if "INSERT INTO auction_listings" in sql:         # INSERT лота
            lot_id, seller, item, price, created = args
            if "ON CONFLICT" in sql and str(lot_id) in self.lots:
                return "INSERT 0 0"                        # миграция: пропустить
            self.lots[str(lot_id)] = {
                "lot_id": str(lot_id), "seller": int(seller), "item": item,
                "price": int(price), "status": "active", "created": created,
                "closed": None, "buyer": None}
            return "INSERT 0 1"

        if "UPDATE characters" in sql:                    # UPDATE gold/inventory
            gold, inv_json, uid = args
            self.characters[int(uid)] = {
                "gold": int(gold), "inventory": json.loads(inv_json)}
            return "UPDATE 1"

        if "UPDATE auction_listings" in sql:              # sold / cancelled
            if "'sold'" in sql:
                buyer, closed, lot_id = args
                lot = self.lots[str(lot_id)]
                lot["status"] = "sold"; lot["buyer"] = int(buyer); lot["closed"] = closed
            else:
                closed, lot_id = args
                lot = self.lots[str(lot_id)]
                lot["status"] = "cancelled"; lot["closed"] = closed
            return "UPDATE 1"

        raise AssertionError("FakeConn.execute: неизвестный SQL:\n" + sql)

    async def fetchrow(self, sql, *args):
        if "COALESCE(SUM" in sql:                         # ledger_total
            uid = int(args[0])
            total = sum(r["gold_delta"] for r in self.ledger.values()
                        if r["uid"] == uid)
            return {"total": total}

        if "FROM economy_ledger" in sql:                  # идемпотентность
            op_id = args[0]
            return {"one": 1} if op_id in self.ledger else None

        if "FROM characters" in sql:                      # SELECT gold, inventory
            uid = int(args[0])
            ch = self.characters.get(uid)
            if ch is None:
                return None
            return {"gold": int(ch["gold"]),
                    "inventory": json.dumps(ch["inventory"])}

        if "FROM auction_listings" in sql:                # SELECT лота FOR UPDATE
            lot = self.lots.get(str(args[0]))
            return dict(lot) if lot is not None else None

        raise AssertionError("FakeConn.fetchrow: неизвестный SQL:\n" + sql)

    async def fetch(self, sql, *args):
        if "FROM auction_listings" in sql and "status='active'" in sql:
            rows = [dict(l) for l in self.lots.values() if l["status"] == "active"]
            rows.sort(key=lambda r: r["price"])
            return rows
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


def build(characters=None, lots=None, ledger=None):
    conn = FakeConn(characters, lots, ledger)
    pool = FakePool(conn)
    return conn, pool.acquire        # (для инспекции таблиц, cf для econ_tx)


def seed_lot(conn, lot_id, seller, item, price, status="active"):
    conn.lots[str(lot_id)] = {"lot_id": str(lot_id), "seller": int(seller),
                              "item": item, "price": int(price), "status": status,
                              "created": 1.0, "closed": None, "buyer": None}


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


def active_ids(conn):
    return {lid for lid, l in conn.lots.items() if l["status"] == "active"}


# ─────────────────────────── сценарии ───────────────────────────
async def scenario_0_list_happy_and_missing(cf_pool):
    """Позитив: выставление снимает ОДИН экземпляр; негатив: предмета нет."""
    conn, cf = build({7: {"gold": 500, "inventory": ["sword", "sword", "gem"]}})
    ok, msg, data = await econ_tx.list_lot(cf, 7, "sword", 200, "L0", "list:L0")
    check(ok is True, "list happy: ok")
    check(data is not None and data["inventory"].count("sword") == 1,
          "list happy: снят один из двух sword")
    check(data["inventory"].count("gem") == 1, "list happy: gem остался")
    check(conn.characters[7]["inventory"].count("sword") == 1,
          "list happy: в БД один sword")
    lots = await econ_tx.load_active_lots(cf)
    check(len(lots) == 1 and lots[0]["id"] == "L0" and lots[0]["item"] == "sword"
          and lots[0]["price"] == 200 and lots[0]["seller_uid"] == 7,
          "list happy: активный лот виден в витрине")
    check(conn.ledger["list:L0"]["operation"] == "auction_list"
          and conn.ledger["list:L0"]["gold_delta"] == 0, "list happy: ledger auction_list, delta 0")
    check(await econ_tx.ledger_total(cf, 7) == 0, "list happy: gold_delta продавца 0")
    # негатив: предмета нет в сумке
    ok2, msg2, d2 = await econ_tx.list_lot(cf, 7, "missing", 100, "L0b", "list:L0b")
    check(ok2 is False and d2 is None, "list negative: отказ, данных нет")
    check("L0b" not in conn.lots, "list negative: лот не создан")


async def scenario_1_rollback(cf_pool):
    """Атомарность: сбой на INSERT лота И сбой на финальном UPDATE — оба откатывают."""
    # 1a: сбой на самом первом write (INSERT auction_listings)
    conn, cf = build({7: {"gold": 1000, "inventory": ["sword"]}})
    conn.fail_on_execute = "INSERT INTO auction_listings"
    ok, msg, data = await econ_tx.list_lot(cf, 7, "sword", 200, "L1", "list:L1")
    check(ok is False and data is None, "rollback-1a: отказ, память не трогаем")
    check("sword" in conn.characters[7]["inventory"], "rollback-1a: предмет на месте")
    check(len(active_ids(conn)) == 0, "rollback-1a: лот не создан")
    check(await econ_tx.ledger_total(cf, 7) == 0, "rollback-1a: ledger пуст")

    # 1b: сбой на ПОСЛЕДНЕМ write (UPDATE characters) — после INSERT лота и ledger.
    # Доказывает реальный откат уже записанных строк, а не просто «ничего не писали».
    conn, cf = build({7: {"gold": 1000, "inventory": ["sword"]}})
    conn.fail_on_execute = "UPDATE characters"
    ok, msg, data = await econ_tx.list_lot(cf, 7, "sword", 200, "L1", "list:L1")
    check(ok is False and data is None, "rollback-1b: отказ")
    check(len(active_ids(conn)) == 0, "rollback-1b: INSERT лота откатан")
    check(await econ_tx.ledger_total(cf, 7) == 0, "rollback-1b: INSERT ledger откатан")
    check("sword" in conn.characters[7]["inventory"], "rollback-1b: предмет на месте")


async def scenario_2_double_buy(cf_pool):
    """Двойная покупка одного лота: второй покупатель получает «недоступен»."""
    conn, cf = build({1: {"gold": 0, "inventory": []},
                      2: {"gold": 5000, "inventory": []},
                      3: {"gold": 5000, "inventory": []}})
    seed_lot(conn, "L2", seller=1, item="gem", price=1000)
    r1 = await econ_tx.buy_lot(cf, 2, "L2", "buy:L2:2")
    r2 = await econ_tx.buy_lot(cf, 3, "L2", "buy:L2:3")
    check(r1[0] is True, "double-buy: первый купил")
    check(r2[0] is False and "недоступ" in r2[1].lower(), "double-buy: второй — недоступен")
    check("gem" in conn.characters[2]["inventory"], "double-buy: предмет у первого")
    check("gem" not in conn.characters[3]["inventory"], "double-buy: у второго предмета нет")
    check(conn.lots["L2"]["status"] == "sold" and conn.lots["L2"]["buyer"] == 2,
          "double-buy: лот sold, покупатель — первый")
    check(conn.characters[3]["gold"] == 5000, "double-buy: золото второго не тронуто")


async def scenario_3_idempotent_retry(cf_pool):
    """Повтор callback с тем же op_id → идемпотентный ответ, балансы не удваиваются."""
    conn, cf = build({1: {"gold": 0, "inventory": []},
                      2: {"gold": 5000, "inventory": []}})
    seed_lot(conn, "L3", seller=1, item="ring", price=1000)
    r1 = await econ_tx.buy_lot(cf, 2, "L3", "buy:L3:2")
    check(r1[0] is True and r1[2]["gold"] == 4000, "retry: первая покупка, gold 4000")
    r2 = await econ_tx.buy_lot(cf, 2, "L3", "buy:L3:2")
    check(r2[0] is True, "retry: повтор — ok (идемпотентно)")
    check(r2[2] is None and r2[3] is None, "retry: данных нет → память не обновится повторно")
    check(conn.characters[2]["gold"] == 4000, "retry: gold покупателя не удвоился")
    check(conn.characters[2]["inventory"].count("ring") == 1, "retry: предмет не удвоился")
    check(await econ_tx.ledger_total(cf, 2) == -1000, "retry: одна запись −1000, не −2000")


async def scenario_4_commit_failure(cf_pool):
    """Исключение на коммите → память не обновляется (данных нет), ledger пуст."""
    conn, cf = build({7: {"gold": 100, "inventory": ["axe"]}})
    conn.fail_commit = True
    ok, msg, data = await econ_tx.list_lot(cf, 7, "axe", 300, "L4", "list:L4")
    check(ok is False, "commit-fail: отказ")
    check(data is None, "commit-fail: данные не возвращены")
    check(await econ_tx.ledger_total(cf, 7) == 0, "commit-fail: ledger пуст (откат)")
    check(len(active_ids(conn)) == 0, "commit-fail: лот не создан")
    check("axe" in conn.characters[7]["inventory"], "commit-fail: предмет на месте")


async def scenario_5_restart_no_reissue(cf_pool):
    """«Рестарт»: после продажи load_active_lots не отдаёт лот; повторная выдача невозможна."""
    conn, cf = build({1: {"gold": 0, "inventory": []},
                      2: {"gold": 5000, "inventory": []},
                      4: {"gold": 5000, "inventory": []}})
    seed_lot(conn, "L5", seller=1, item="cloak", price=800)
    await econ_tx.buy_lot(cf, 2, "L5", "buy:L5:2")
    lots_after = await econ_tx.load_active_lots(cf)     # эмуляция чтения после рестарта
    check("L5" not in {l["id"] for l in lots_after}, "restart: проданный лот не в витрине")
    check(len(lots_after) == 0, "restart: активных лотов нет")
    r = await econ_tx.buy_lot(cf, 4, "L5", "buy:L5:4")
    check(r[0] is False and "недоступ" in r[1].lower(), "restart: повторная выдача невозможна")


async def scenario_6_offline_seller(cf_pool):
    """Оффлайн-продавец: его data обновлена в «БД» и возвращена вызывающему."""
    conn, cf = build({1: {"gold": 100, "inventory": []},
                      2: {"gold": 5000, "inventory": []}})
    seed_lot(conn, "L6", seller=1, item="boots", price=1000)
    ok, msg, bd, sd, lot = await econ_tx.buy_lot(cf, 2, "L6", "buy:L6:2")
    proceeds = int(1000 * 0.95)
    check(ok is True, "offline: покупка ок")
    check(sd is not None and sd["gold"] == 100 + proceeds, "offline: seller_data содержит зачисление")
    check(conn.characters[1]["gold"] == 100 + proceeds, "offline: строка продавца в БД обновлена")
    check(await econ_tx.ledger_total(cf, 1) == proceeds, "offline: ledger продавца = выручка")


async def scenario_7_cancel_foreign(cf_pool):
    """Снятие чужого лота — отказ; снятие своего — возврат предмета и ledger."""
    conn, cf = build({1: {"gold": 0, "inventory": []},
                      9: {"gold": 0, "inventory": []}})
    seed_lot(conn, "L7", seller=1, item="wand", price=500)
    ok, msg, data = await econ_tx.cancel_lot(cf, 9, "L7", "cancel:L7")   # 9 — не владелец
    check(ok is False and data is None, "cancel-foreign: отказ")
    check(conn.lots["L7"]["status"] == "active", "cancel-foreign: лот всё ещё активен")
    check("wand" not in conn.characters[9]["inventory"], "cancel-foreign: предмет не ушёл чужому")
    check(conn.characters[1]["inventory"] == [], "cancel-foreign: у владельца тоже без изменений")
    # владелец снимает свой лот (тем же op_id — чужой отказ не «отравил» идемпотентность)
    ok2, msg2, d2 = await econ_tx.cancel_lot(cf, 1, "L7", "cancel:L7")
    check(ok2 is True, "cancel-own: ок")
    check("wand" in conn.characters[1]["inventory"], "cancel-own: предмет вернулся владельцу")
    check(conn.lots["L7"]["status"] == "cancelled", "cancel-own: лот отменён")
    check("cancel:L7" in conn.ledger and conn.ledger["cancel:L7"]["operation"] == "auction_cancel",
          "cancel-own: ledger auction_cancel")


async def scenario_8_fee_and_double_entry(cf_pool):
    """Комиссия 5% (строка auction_fee, uid=0) и двойная запись (сумма дельт)."""
    conn, cf = build({1: {"gold": 0, "inventory": []},
                      2: {"gold": 5000, "inventory": []}})
    seed_lot(conn, "L8", seller=1, item="crown", price=1000)
    await econ_tx.buy_lot(cf, 2, "L8", "buy:L8:2")
    fee_row = conn.ledger.get("buy:L8:2:fee")
    check(fee_row is not None and fee_row["operation"] == "auction_fee", "fee: строка auction_fee есть")
    check(fee_row["uid"] == 0 and fee_row["gold_delta"] == 50, "fee: uid=0, комиссия +50 (5%)")
    buyer_delta = await econ_tx.ledger_total(cf, 2)
    seller_delta = await econ_tx.ledger_total(cf, 1)
    sink_delta = await econ_tx.ledger_total(cf, 0)
    check(buyer_delta == -1000, "fee: покупатель −1000")
    check(seller_delta == 950, "fee: продавец +950")
    check(sink_delta == 50, "fee: сток +50")
    check(buyer_delta + seller_delta == -50, "fee: сумма дельт участников = −комиссия")
    check(buyer_delta + seller_delta + sink_delta == 0, "fee: двойная запись (сумма всех = 0)")


async def scenario_9_insufficient_gold(cf_pool):
    """Недостаток золота → отказ, ничего не изменилось."""
    conn, cf = build({1: {"gold": 0, "inventory": []},
                      2: {"gold": 500, "inventory": []}})
    seed_lot(conn, "L9", seller=1, item="shield", price=1000)
    ok, msg, bd, sd, lot = await econ_tx.buy_lot(cf, 2, "L9", "buy:L9:2")
    check(ok is False and "хватает" in msg.lower(), "low-gold: отказ")
    check(bd is None and sd is None, "low-gold: данных нет")
    check(conn.characters[2]["gold"] == 500, "low-gold: золото покупателя не тронуто")
    check(conn.lots["L9"]["status"] == "active", "low-gold: лот активен")
    check("shield" not in conn.characters[2]["inventory"], "low-gold: предмет не ушёл")
    check(await econ_tx.ledger_total(cf, 2) == 0 and await econ_tx.ledger_total(cf, 1) == 0,
          "low-gold: ledger пуст")


async def scenario_10_two_buys_one_buyer(cf_pool):
    """Две покупки РАЗНЫХ лотов одним uid → обе успешны."""
    conn, cf = build({1: {"gold": 0, "inventory": []},
                      5: {"gold": 0, "inventory": []},
                      2: {"gold": 5000, "inventory": []}})
    seed_lot(conn, "L10a", seller=1, item="potion", price=1000)
    seed_lot(conn, "L10b", seller=5, item="elixir", price=800)
    r1 = await econ_tx.buy_lot(cf, 2, "L10a", "buy:L10a:2")
    r2 = await econ_tx.buy_lot(cf, 2, "L10b", "buy:L10b:2")
    check(r1[0] is True and r2[0] is True, "two-buys: обе покупки ок")
    check(conn.characters[2]["gold"] == 5000 - 1000 - 800, "two-buys: gold = 3200")
    check("potion" in conn.characters[2]["inventory"] and "elixir" in conn.characters[2]["inventory"],
          "two-buys: оба предмета в сумке")
    check(conn.lots["L10a"]["status"] == "sold" and conn.lots["L10b"]["status"] == "sold",
          "two-buys: оба лота проданы")
    check(await econ_tx.ledger_total(cf, 2) == -1800, "two-buys: суммарная дельта покупателя −1800")


async def scenario_11_migration(cf_pool):
    """Миграция активных лотов из памяти в БД идемпотентна (ON CONFLICT DO NOTHING)."""
    conn, cf = build({})
    lots = [{"id": "m1", "seller_uid": 7, "item": "x", "price": 100, "ts": 123.0},
            {"id": "m2", "seller_uid": 8, "item": "y", "price": 50}]
    n = await econ_tx.import_lots(cf, lots)
    check(n == 2, "migration: обработано 2 лота")
    active = await econ_tx.load_active_lots(cf)
    check(len(active) == 2, "migration: 2 активных лота в БД")
    check(active[0]["id"] == "m2", "migration: витрина сортирует по цене (m2 дешевле)")
    await econ_tx.import_lots(cf, lots)                # повторная миграция
    check(len(await econ_tx.load_active_lots(cf)) == 2, "migration: повтор не задублировал")


async def run_all():
    for fn in (scenario_0_list_happy_and_missing, scenario_1_rollback,
               scenario_2_double_buy, scenario_3_idempotent_retry,
               scenario_4_commit_failure, scenario_5_restart_no_reissue,
               scenario_6_offline_seller, scenario_7_cancel_foreign,
               scenario_8_fee_and_double_entry, scenario_9_insufficient_gold,
               scenario_10_two_buys_one_buyer, scenario_11_migration):
        await fn(None)


def main():
    asyncio.run(run_all())
    print(f"ИТОГО: ✅ {_passed} пройдено, ❌ {_failed} провалено")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
