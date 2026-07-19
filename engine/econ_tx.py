# -*- coding: utf-8 -*-
"""
Транзакционное ядро экономики (Этап 3.1): аукцион + журнал economy_ledger.

Зачем: раньше аукцион жил в памяти со снапшотом в kv_state раз в 60с — падение
процесса теряло или дублировало предметы и золото. Здесь каждая операция — одна
атомарная БД-транзакция с двойной записью в economy_ledger, поэтому предмет и
золото не пропадают и не удваиваются даже при обрыве процесса на середине.

Слой без Telegram (engine/): все функции принимают cf (conn_factory) —
async-контекст-менеджер, отдающий соединение с интерфейсом asyncpg
(.transaction()/.execute()/.fetchrow()/.fetch()). В бою это db.pool.acquire,
в тестах — мок без Postgres.

Формат персонажа (сверено с engine/db.py save/load_all): таблица characters,
gold — колонка BIGINT, inventory — колонка JSONB (список строк-ключей предметов,
дубликаты допустимы). asyncpg отдаёт JSONB строкой — разбираем json.loads.

Идемпотентность: каждая операция кладёт строку в economy_ledger с
operation_id = op_id (PRIMARY KEY). Повтор того же op_id (ретрай callback'а)
не меняет балансы дважды: быстрый путь — предварительный SELECT по operation_id;
страховка от гонки — сам PRIMARY KEY (duplicate key внутри транзакции → откат →
идемпотентный ответ).

Комиссия: с продажи удерживается AUCTION_FEE (5%). Выручка продавца —
int(price*(1-FEE)); удержанное — сток золота (строка ledger с uid=0). Сумма
дельт покупателя и продавца по проданному лоту = −комиссия (золото уходит из
оборота); сумма всех трёх строк ledger (включая сток uid=0) = 0 (двойная запись).
"""
import json
import time

# Зеркалит engine/auction.AUCTION_FEE (5%). Держим локально, чтобы ядро было
# самодостаточным и не тянуло зависимостей ради одной константы.
AUCTION_FEE = 0.05

LEDGER_SQL = (
    "INSERT INTO economy_ledger "
    "(operation_id, uid, operation, gold_delta, item, counterparty, created) "
    "VALUES ($1,$2,$3,$4,$5,$6,$7)"
)


def _is_duplicate(exc: Exception) -> bool:
    """Похоже ли исключение на нарушение PRIMARY KEY economy_ledger (гонка
    двух одинаковых op_id). asyncpg бросает UniqueViolationError; мок в тестах —
    обычное исключение с 'duplicate key' в тексте. Ловим оба варианта."""
    if exc.__class__.__name__ == "UniqueViolationError":
        return True
    return "duplicate key" in str(exc).lower()


def _inv(raw):
    """Инвентарь из БД → список. asyncpg отдаёт JSONB строкой (как в db.py
    load_all: json.loads(r['inventory'])); переживём и готовый список/None."""
    if raw is None:
        return []
    if isinstance(raw, (str, bytes, bytearray)):
        return json.loads(raw)
    return list(raw)


async def _lock_char(conn, uid):
    """Заблокировать строку персонажа (FOR UPDATE) и вернуть его изменяемый
    снимок {'gold': int, 'inventory': [..]} или None, если персонажа нет."""
    row = await conn.fetchrow(
        "SELECT gold, inventory FROM characters WHERE uid=$1 FOR UPDATE", uid)
    if row is None:
        return None
    return {"gold": int(row["gold"]), "inventory": _inv(row["inventory"])}


async def _persist_char(conn, uid, ch):
    """Записать gold/inventory персонажа обратно (inventory → JSONB-строкой)."""
    await conn.execute(
        "UPDATE characters SET gold=$1, inventory=$2 WHERE uid=$3",
        int(ch["gold"]), json.dumps(ch["inventory"]), uid)


async def _op_done(conn, op_id) -> bool:
    """Быстрый путь идемпотентности: уже есть строка ledger с этим op_id?"""
    return bool(await conn.fetchrow(
        "SELECT 1 FROM economy_ledger WHERE operation_id=$1", op_id))


# ───────────────────────── выставление лота ─────────────────────────
async def list_lot(cf, seller_uid, item, price, lot_id, op_id):
    """Выставить предмет на аукцион одной транзакцией.

    Порядок: идемпотентность → блокировка продавца FOR UPDATE → проверка, что
    предмет в сумке → снять один экземпляр → INSERT лота (active) → INSERT
    ledger('auction_list', gold_delta=0) → UPDATE персонажа. Возврат seller_data
    ({'gold','inventory'}) — обновить память ПОСЛЕ коммита; None, если менять
    память не нужно (идемпотентный повтор/ошибка).

    → (ok: bool, msg: str, seller_data | None)
    """
    price = int(price)
    async with cf() as conn:
        if await _op_done(conn, op_id):
            return True, "Лот уже выставлен.", None
        now = time.time()
        try:
            async with conn.transaction():
                seller = await _lock_char(conn, seller_uid)
                if seller is None:
                    result = (False, "Персонаж не найден.", None)
                elif item not in seller["inventory"]:
                    result = (False, "Предмета нет в сумке.", None)
                else:
                    seller["inventory"].remove(item)   # снять один экземпляр
                    await conn.execute(
                        "INSERT INTO auction_listings "
                        "(lot_id, seller, item, price, status, created) "
                        "VALUES ($1,$2,$3,$4,'active',$5)",
                        lot_id, seller_uid, item, price, now)
                    await conn.execute(
                        LEDGER_SQL, op_id, seller_uid, "auction_list",
                        0, item, None, now)
                    await _persist_char(conn, seller_uid, seller)
                    result = (True, "Лот выставлен.",
                              {"gold": seller["gold"], "inventory": seller["inventory"]})
        except Exception as exc:
            if _is_duplicate(exc):
                return True, "Лот уже выставлен.", None
            return False, "⚙️ Торговля временно недоступна.", None
        return result


# ───────────────────────── покупка лота ─────────────────────────
async def buy_lot(cf, buyer_uid, lot_id, op_id):
    """Купить лот одной транзакцией.

    Порядок: идемпотентность → SELECT лота FOR UPDATE (нет/не active →
    недоступен; свой лот → отказ) → блокировка покупателя и продавца FOR UPDATE
    В ПОРЯДКЕ ВОЗРАСТАНИЯ uid (профилактика дедлока) → проверка золота из БД →
    покупатель gold−price, +предмет; продавец gold+int(price*0.95); лот → sold →
    3 строки ledger (buyer −price / seller +выручка / fee uid=0 +комиссия) →
    COMMIT.

    → (ok, msg, buyer_data | None, seller_data | None, lot | None)
    """
    async with cf() as conn:
        if await _op_done(conn, op_id):
            return True, "Покупка уже обработана.", None, None, None
        now = time.time()
        try:
            async with conn.transaction():
                lot = await conn.fetchrow(
                    "SELECT lot_id, seller, item, price, status "
                    "FROM auction_listings WHERE lot_id=$1 FOR UPDATE", lot_id)
                if lot is None or lot["status"] != "active":
                    result = (False, "Лот недоступен.", None, None, None)
                elif int(lot["seller"]) == int(buyer_uid):
                    result = (False, "Это ваш лот.", None, None, None)
                else:
                    seller_uid = int(lot["seller"])
                    price = int(lot["price"])
                    item = lot["item"]
                    # блокируем обе строки персонажей по возрастанию uid
                    lo, hi = sorted((int(buyer_uid), seller_uid))
                    c_lo = await _lock_char(conn, lo)
                    c_hi = await _lock_char(conn, hi)
                    buyer = c_lo if lo == int(buyer_uid) else c_hi
                    seller = c_lo if lo == seller_uid else c_hi
                    if buyer is None or seller is None:
                        result = (False, "Персонаж не найден.", None, None, None)
                    elif buyer["gold"] < price:
                        result = (False, "Не хватает монет.", None, None, None)
                    else:
                        proceeds = int(price * (1 - AUCTION_FEE))
                        fee = price - proceeds
                        buyer["gold"] -= price
                        buyer["inventory"].append(item)
                        seller["gold"] += proceeds
                        await _persist_char(conn, int(buyer_uid), buyer)
                        await _persist_char(conn, seller_uid, seller)
                        await conn.execute(
                            "UPDATE auction_listings SET status='sold', "
                            "buyer=$1, closed=$2 WHERE lot_id=$3",
                            int(buyer_uid), now, lot_id)
                        await conn.execute(
                            LEDGER_SQL, op_id, int(buyer_uid), "auction_buy",
                            -price, item, seller_uid, now)
                        await conn.execute(
                            LEDGER_SQL, op_id + ":seller", seller_uid,
                            "auction_sale", proceeds, item, int(buyer_uid), now)
                        await conn.execute(
                            LEDGER_SQL, op_id + ":fee", 0, "auction_fee",
                            fee, item, None, now)
                        result = (
                            True, "Покупка совершена.",
                            {"gold": buyer["gold"], "inventory": buyer["inventory"]},
                            {"gold": seller["gold"], "inventory": seller["inventory"]},
                            {"id": lot_id, "seller_uid": seller_uid,
                             "item": item, "price": price, "proceeds": proceeds})
        except Exception as exc:
            if _is_duplicate(exc):
                return True, "Покупка уже обработана.", None, None, None
            return False, "⚙️ Торговля временно недоступна.", None, None, None
        return result


# ───────────────────────── снятие лота ─────────────────────────
async def cancel_lot(cf, seller_uid, lot_id, op_id):
    """Снять свой активный лот и вернуть предмет в сумку.

    Порядок: идемпотентность → SELECT лота FOR UPDATE (нет/не active →
    недоступен; чужой → отказ) → блокировка продавца → предмет обратно в
    инвентарь → лот → cancelled → ledger('auction_cancel', 0) → COMMIT.

    → (ok, msg, seller_data | None)
    """
    async with cf() as conn:
        if await _op_done(conn, op_id):
            return True, "Лот уже снят.", None
        now = time.time()
        try:
            async with conn.transaction():
                lot = await conn.fetchrow(
                    "SELECT lot_id, seller, item, price, status "
                    "FROM auction_listings WHERE lot_id=$1 FOR UPDATE", lot_id)
                if lot is None or lot["status"] != "active":
                    result = (False, "Лот недоступен.", None)
                elif int(lot["seller"]) != int(seller_uid):
                    result = (False, "Это не ваш лот.", None)
                else:
                    seller = await _lock_char(conn, seller_uid)
                    if seller is None:
                        result = (False, "Персонаж не найден.", None)
                    else:
                        item = lot["item"]
                        seller["inventory"].append(item)
                        await conn.execute(
                            "UPDATE auction_listings SET status='cancelled', "
                            "closed=$1 WHERE lot_id=$2", now, lot_id)
                        await conn.execute(
                            LEDGER_SQL, op_id, int(seller_uid),
                            "auction_cancel", 0, item, None, now)
                        await _persist_char(conn, seller_uid, seller)
                        result = (True, "Лот снят, предмет возвращён в сумку.",
                                  {"gold": seller["gold"], "inventory": seller["inventory"]})
        except Exception as exc:
            if _is_duplicate(exc):
                return True, "Лот уже снят.", None
            return False, "⚙️ Торговля временно недоступна.", None
        return result


# ───────────────────────── чтение / миграция / сверка ─────────────────────────
async def load_active_lots(cf):
    """Активные лоты для витрины (дешевле цены — выше). Ключи совпадают с
    внутриигровым форматом лота (id/seller_uid/item/price) — рендер в bot/ без
    переименований; seller_name витрина подставляет из памяти персонажей."""
    async with cf() as conn:
        rows = await conn.fetch(
            "SELECT lot_id, seller, item, price, created "
            "FROM auction_listings WHERE status='active' ORDER BY price ASC")
    return [{"id": r["lot_id"], "seller_uid": int(r["seller"]),
             "item": r["item"], "price": int(r["price"]),
             "created": r["created"]} for r in rows]


async def import_lots(cf, lots):
    """Одноразовая миграция активных лотов из памяти/kv_state/файла в БД.
    Идемпотентна (ON CONFLICT DO NOTHING). Возвращает число обработанных лотов.
    lots — список dict-ов формата AuctionManager (id/seller_uid/item/price/ts)."""
    n = 0
    async with cf() as conn:
        async with conn.transaction():
            for l in lots:
                await conn.execute(
                    "INSERT INTO auction_listings "
                    "(lot_id, seller, item, price, status, created) "
                    "VALUES ($1,$2,$3,$4,'active',$5) "
                    "ON CONFLICT (lot_id) DO NOTHING",
                    str(l["id"]), int(l["seller_uid"]), l["item"],
                    int(l["price"]), l.get("ts") or time.time())
                n += 1
    return n


async def ledger_total(cf, uid):
    """Сумма gold_delta по uid (для тестов согласованности двойной записи)."""
    async with cf() as conn:
        row = await conn.fetchrow(
            "SELECT COALESCE(SUM(gold_delta),0) AS total "
            "FROM economy_ledger WHERE uid=$1", int(uid))
    return int(row["total"]) if row and row["total"] is not None else 0
