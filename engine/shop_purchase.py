"""Durable MAX shop confirmations; one item transfer per opaque token."""
import asyncio
import json
import re
import time
import uuid

from . import content, game_actions, money
from .econ_tx import LEDGER_SQL
from .lifecycle_errors import StaleCharacterWrite

TOKEN = re.compile(r'[0-9a-f]{32}\Z')


def guard(ch):
    if ch.uid >= 0:
        return 'Эта торговая операция доступна только в MAX.'
    if ch.flags.get('dead') or ch.hp <= 0 or ch.target:
        return 'Торговля недоступна в бою или после смерти.'


def _apply_delta(ch, gold_delta, item, item_delta):
    if item_delta < 0 and item not in ch.inventory:
        raise RuntimeError('Sold item missing from cache; reconciliation required')
    if item_delta > 0:
        ch.inventory.append(item)
    else:
        ch.inventory.remove(item)
    ch.gold += gold_delta


async def recover(db, ch):
    """Called under the character-write lock before saves and further purchases."""
    uncertain = getattr(db, '_shop_uncertain', {}).get(ch.uid)
    if uncertain is None:
        return
    token, generation, gold_delta, item, item_delta = uncertain
    # Lock the intent: a plain SELECT could observe 'pending' while an earlier
    # COMMIT is still in flight, then let the next save overwrite its result.
    async with db.pool.acquire() as con:
        async with con.transaction():
            row = await con.fetchrow('SELECT status FROM max_shop_intents WHERE token=$1 AND uid=$2 FOR UPDATE', token, ch.uid)
    if row is None:
        raise RuntimeError('Purchase outcome unavailable; snapshot save blocked')
    if row['status'] == 'done' and ch.generation == generation:
        # Preserve rewards/loot accumulated in memory during the network failure.
        _apply_delta(ch, gold_delta, item, item_delta)
    db._shop_uncertain.pop(ch.uid, None)


class ShopPurchaseStore:
    def __init__(self, db):
        self.db = db

    async def quote(self, ch, vendor, item, operation='buy'):
        if operation not in ('buy', 'sell'):
            raise ValueError('Unsupported shop operation')
        error = guard(ch)
        if error:
            return False, error, None
        db = self.db
        async with db._character_write_locks.setdefault(ch.uid, asyncio.Lock()):
            await db._save_character(ch)
            async with db.pool.acquire() as con:
                async with con.transaction():
                    row = await con.fetchrow('SELECT * FROM characters WHERE uid=$1 FOR UPDATE', ch.uid)
                    self._current(row, ch)
                    actor = db._row_to_char(row)
                    error = guard(ch)
                    if error:
                        return False, error, None
                    # A copy checks stock, class, reputation and funds without granting anything.
                    vendor, _stock = game_actions.shop_stock_here(actor, vendor)
                    if vendor is None:
                        return False, 'Торговца рядом нет. Выберите торговца заново.', None
                    action = game_actions.shop_buy_here if operation == 'buy' else game_actions.shop_sell_here
                    ok, message = action(actor, item, vendor)
                    if not ok:
                        return False, message, None
                    price = abs(int(row['gold']) - actor.gold)
                    token = uuid.uuid4().hex
                    await con.execute("UPDATE max_shop_intents SET status='cancelled' WHERE uid=$1 AND status='pending'", ch.uid)
                    await con.execute('''INSERT INTO max_shop_intents(token,uid,generation,room,vendor,item,price,operation)
                        VALUES($1,$2,$3,$4,$5,$6,$7,$8)''', token, ch.uid, ch.generation, actor.room, vendor, item, price, operation)
            header = '🛍 Покупка' if operation == 'buy' else '💰 Продажа'
            price_label = 'Цена' if operation == 'buy' else 'Вы получите'
            return True, (f"{header}: {content.ITEMS[item]['name']} · 1 шт.\n"
                          f"{price_label}: 💰{money.fmt(price)}.\nПодтвердите операцию в течение 5 минут.\n"
                          'Новый выбор для покупки или продажи отменяет предыдущее подтверждение.'), token

    @staticmethod
    def _current(row, ch):
        if row is None or row['deleted_at'] is not None or int(row['generation']) != ch.generation:
            raise StaleCharacterWrite('Торговля устаревшего героя запрещена.')

    async def confirm(self, ch, token, cancel=False, operation='buy'):
        if operation not in ('buy', 'sell'):
            raise ValueError('Unsupported shop operation')
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            return False, 'Некорректное подтверждение покупки.'
        db = self.db
        async with db._character_write_locks.setdefault(ch.uid, asyncio.Lock()):
            await db._save_character(ch)
            committing = None
            try:
                async with db.pool.acquire() as con:
                    async with con.transaction():
                        row = await con.fetchrow('SELECT * FROM characters WHERE uid=$1 FOR UPDATE', ch.uid)
                        self._current(row, ch)
                        intent = await con.fetchrow('''SELECT *, expires_at <= now() AS expired
                            FROM max_shop_intents WHERE token=$1 AND uid=$2 FOR UPDATE''', token, ch.uid)
                        if intent is None or intent['generation'] != ch.generation:
                            return False, 'Это подтверждение не принадлежит текущему герою.'
                        if intent['operation'] != operation:
                            return False, 'Тип подтверждения не совпадает. Используйте исходную кнопку.'
                        if intent['status'] == 'done':
                            return True, 'Операция уже выполнена. Повторного изменения золота и предметов нет.\n' + intent['receipt']
                        if intent['status'] != 'pending' or intent['expired']:
                            return False, 'Подтверждение отменено или истекло. Выберите товар заново.'
                        if cancel:
                            await con.execute("UPDATE max_shop_intents SET status='cancelled' WHERE token=$1", token)
                            return True, 'Операция отменена. Золото и предметы не изменены.'
                        error = guard(ch)
                        actor = db._row_to_char(row)
                        if error:
                            return False, error
                        if actor.room != intent['room'] or ch.room != intent['room']:
                            return False, 'Вы покинули торговца. Выберите товар заново.'
                        vendor, stock = game_actions.shop_stock_here(actor, intent['vendor'])
                        if operation == 'buy':
                            available = {key: game_actions.shop_price(actor, key, vendor) for key in stock
                                         if key in content.ITEMS} if vendor is not None else {}
                        else:
                            available = dict(game_actions.shop_sellable_here(actor, intent['vendor']))
                        if intent['item'] not in available:
                            return False, 'Предмет больше недоступен для этой операции у торговца.'
                        if available[intent['item']] != intent['price']:
                            return False, 'Цена изменилась. Выберите товар заново.'
                        action = game_actions.shop_buy_here if operation == 'buy' else game_actions.shop_sell_here
                        ok, receipt = action(actor, intent['item'], intent['vendor'])
                        if not ok:
                            return False, receipt
                        await con.execute('UPDATE characters SET gold=$2,inventory=$3,updated_at=now() WHERE uid=$1',
                                          ch.uid, actor.gold, json.dumps(actor.inventory))
                        gold_delta = -intent['price'] if operation == 'buy' else intent['price']
                        item_delta = 1 if operation == 'buy' else -1
                        for suffix, uid, delta in (('buyer' if operation == 'buy' else 'seller', ch.uid, gold_delta), ('sink', 0, -gold_delta)):
                            await con.execute(LEDGER_SQL, f'maxshop:{token}:{suffix}', uid, f'shop_{operation}', delta,
                                              intent['item'], None, time.time())
                        await con.execute("UPDATE max_shop_intents SET status='done',receipt=$2 WHERE token=$1", token, receipt)
                        committing = (token, ch.generation, gold_delta, intent['item'], item_delta)
                # No await between successful COMMIT and cache publication.
                _apply_delta(ch, gold_delta, intent['item'], item_delta)
                return True, receipt
            except BaseException:
                if committing is not None:
                    if not hasattr(db, '_shop_uncertain'):
                        db._shop_uncertain = {}
                    db._shop_uncertain[ch.uid] = committing
                raise
