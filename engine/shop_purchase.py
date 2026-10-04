"""Durable MAX shop confirmations; one item and one debit per opaque token."""
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
        return 'Эта покупка доступна только в MAX.'
    if ch.flags.get('dead') or ch.hp <= 0 or ch.target:
        return 'Покупка недоступна в бою или после смерти.'


async def recover(db, ch):
    """Called under the character-write lock before saves and further purchases."""
    uncertain = getattr(db, '_shop_uncertain', {}).get(ch.uid)
    if uncertain is None:
        return
    token, generation, price, item = uncertain
    # Lock the intent: a plain SELECT could observe 'pending' while an earlier
    # COMMIT is still in flight, then let the next save overwrite its result.
    async with db.pool.acquire() as con:
        async with con.transaction():
            row = await con.fetchrow('SELECT status FROM max_shop_intents WHERE token=$1 AND uid=$2 FOR UPDATE', token, ch.uid)
    if row is None:
        raise RuntimeError('Purchase outcome unavailable; snapshot save blocked')
    if row['status'] == 'done' and ch.generation == generation:
        # Preserve rewards/loot accumulated in memory during the network failure.
        ch.gold -= price
        ch.inventory.append(item)
    db._shop_uncertain.pop(ch.uid, None)


class ShopPurchaseStore:
    def __init__(self, db):
        self.db = db

    async def quote(self, ch, vendor, item):
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
                    ok, message = game_actions.shop_buy_here(actor, item, vendor)
                    if not ok:
                        return False, message, None
                    price = int(row['gold']) - actor.gold
                    token = uuid.uuid4().hex
                    await con.execute("UPDATE max_shop_intents SET status='cancelled' WHERE uid=$1 AND status='pending'", ch.uid)
                    await con.execute('''INSERT INTO max_shop_intents(token,uid,generation,room,vendor,item,price)
                        VALUES($1,$2,$3,$4,$5,$6,$7)''', token, ch.uid, ch.generation, actor.room, vendor, item, price)
            return True, (f"🛍 {content.ITEMS[item]['name']} · 1 шт.\n"
                          f"Цена: 💰{money.fmt(price)}.\nПодтвердите покупку в течение 5 минут.\n"
                          'Новый выбор товара отменяет предыдущее подтверждение.'), token

    @staticmethod
    def _current(row, ch):
        if row is None or row['deleted_at'] is not None or int(row['generation']) != ch.generation:
            raise StaleCharacterWrite('Покупка устаревшего героя запрещена.')

    async def confirm(self, ch, token, cancel=False):
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
                        if intent['status'] == 'done':
                            return True, 'Покупка уже выполнена. Повторного списания нет.\n' + intent['receipt']
                        if intent['status'] != 'pending' or intent['expired']:
                            return False, 'Подтверждение отменено или истекло. Выберите товар заново.'
                        if cancel:
                            await con.execute("UPDATE max_shop_intents SET status='cancelled' WHERE token=$1", token)
                            return True, 'Покупка отменена. Золото не списано.'
                        error = guard(ch)
                        actor = db._row_to_char(row)
                        if error:
                            return False, error
                        if actor.room != intent['room'] or ch.room != intent['room']:
                            return False, 'Вы покинули торговца. Выберите товар заново.'
                        vendor, stock = game_actions.shop_stock_here(actor, intent['vendor'])
                        if vendor is None or intent['item'] not in stock or intent['item'] not in content.ITEMS:
                            return False, 'Товар больше недоступен у этого торговца.'
                        if game_actions.shop_price(actor, intent['item'], vendor) != intent['price']:
                            return False, 'Цена изменилась. Выберите товар заново.'
                        ok, receipt = game_actions.shop_buy_here(actor, intent['item'], vendor)
                        if not ok:
                            return False, receipt
                        await con.execute('UPDATE characters SET gold=$2,inventory=$3,updated_at=now() WHERE uid=$1',
                                          ch.uid, actor.gold, json.dumps(actor.inventory))
                        for suffix, uid, delta in (('buyer', ch.uid, -intent['price']), ('sink', 0, intent['price'])):
                            await con.execute(LEDGER_SQL, f'maxshop:{token}:{suffix}', uid, 'shop_buy', delta,
                                              intent['item'], None, time.time())
                        await con.execute("UPDATE max_shop_intents SET status='done',receipt=$2 WHERE token=$1", token, receipt)
                        committing = (token, ch.generation, intent['price'], intent['item'])
                # No await between successful COMMIT and cache publication.
                ch.gold -= intent['price']
                ch.inventory.append(intent['item'])
                return True, receipt
            except BaseException:
                if committing is not None:
                    if not hasattr(db, '_shop_uncertain'):
                        db._shop_uncertain = {}
                    db._shop_uncertain[ch.uid] = committing
                raise
