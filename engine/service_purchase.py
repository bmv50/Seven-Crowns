"""Durable MAX repair/learning quotes; no gold or progress before confirmation."""
import asyncio
import json
import time
import uuid

from . import content, game_actions, money
from .character import DURAB_MAX, WEAR_SLOTS
from .econ_tx import LEDGER_SQL
from .shop_purchase import TOKEN, ShopPurchaseStore, guard


def _snapshot(ch, operation):
    if operation == 'repair':
        return {slot: [ch.equipment[slot], ch.durab(slot)] for slot in WEAR_SLOTS
                if ch.equipment.get(slot)}
    return None


def _action(ch, operation, subject):
    return (game_actions.repair_here(ch) if operation == 'repair'
            else game_actions.learn_skill_here(ch, subject))


def _provider(ch, operation):
    if operation == 'repair':
        return 'кузнец' if 'кузнец' in content.WORLD.get(ch.room, {}).get('npc', []) else None
    return game_actions.trainer_here(ch)


def _apply(ch, gold_delta, patch):
    # Preserve unrelated rewards and wear acquired while COMMIT was in flight.
    for slot, (item, delta) in patch.get('durab', {}).items():
        if ch.equipment.get(slot) != item:
            raise RuntimeError('Repair equipment changed during reconciliation')
    ch.gold += gold_delta
    for slot, (item, delta) in patch.get('durab', {}).items():
        ch.set_durab(slot, ch.durab(slot) + delta)
    for field in ('learned', 'loadout'):
        for sid in patch.get(field, []):
            if sid not in getattr(ch, field):
                getattr(ch, field).append(sid)


async def recover(db, ch):
    uncertain = getattr(db, '_service_uncertain', {}).get(ch.uid)
    if uncertain is None:
        return
    token, generation, gold_delta, patch = uncertain
    async with db.pool.acquire() as con:
        async with con.transaction():
            row = await con.fetchrow('SELECT status FROM max_service_intents WHERE token=$1 AND uid=$2 FOR UPDATE', token, ch.uid)
    if row is None:
        raise RuntimeError('Service outcome unavailable; snapshot save blocked')
    if row['status'] == 'done' and ch.generation == generation:
        _apply(ch, gold_delta, patch)
    db._service_uncertain.pop(ch.uid, None)


class ServicePurchaseStore:
    def __init__(self, db):
        self.db = db

    async def quote(self, ch, operation, subject=''):
        if operation not in ('repair', 'learn'):
            raise ValueError('Unsupported service')
        error = guard(ch)
        if error:
            return False, error, None
        db = self.db
        async with db._character_write_locks.setdefault(ch.uid, asyncio.Lock()):
            await db._save_character(ch)
            async with db.pool.acquire() as con:
                async with con.transaction():
                    row = await con.fetchrow('SELECT * FROM characters WHERE uid=$1 FOR UPDATE', ch.uid)
                    ShopPurchaseStore._current(row, ch)
                    actor = db._row_to_char(row)
                    error = guard(ch)
                    if error:
                        return False, error, None
                    provider = _provider(actor, operation)
                    snapshot = _snapshot(actor, operation)
                    ok, message = _action(actor, operation, subject)
                    if not ok:
                        return False, message, None
                    price = int(row['gold']) - actor.gold
                    token = uuid.uuid4().hex
                    await con.execute("UPDATE max_service_intents SET status='cancelled' WHERE uid=$1 AND status='pending'", ch.uid)
                    await con.execute('''INSERT INTO max_service_intents
                        (token,uid,generation,room,provider,operation,subject,price,snapshot)
                        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)''', token, ch.uid, ch.generation,
                        actor.room, provider, operation, subject, price, json.dumps(snapshot))
            title = '🔧 Ремонт снаряжения' if operation == 'repair' else '🎓 ' + content.SKILLS[subject]['name']
            return True, (f'{title}\nЦена: 💰{money.fmt(price)}.\n'
                          'Подтвердите в течение 5 минут. Новый выбор услуги отменяет предыдущий.'), token

    async def confirm(self, ch, token, operation, cancel=False):
        if operation not in ('repair', 'learn'):
            raise ValueError('Unsupported service')
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            return False, 'Некорректное подтверждение услуги.'
        db = self.db
        async with db._character_write_locks.setdefault(ch.uid, asyncio.Lock()):
            await db._save_character(ch)
            committing = None
            try:
                async with db.pool.acquire() as con:
                    async with con.transaction():
                        row = await con.fetchrow('SELECT * FROM characters WHERE uid=$1 FOR UPDATE', ch.uid)
                        ShopPurchaseStore._current(row, ch)
                        intent = await con.fetchrow('''SELECT *, expires_at <= now() AS expired
                            FROM max_service_intents WHERE token=$1 AND uid=$2 FOR UPDATE''', token, ch.uid)
                        if intent is None or intent['generation'] != ch.generation:
                            return False, 'Это подтверждение не принадлежит текущему герою.'
                        if intent['operation'] != operation:
                            return False, 'Тип подтверждения не совпадает. Используйте исходную кнопку.'
                        if intent['status'] == 'done':
                            return True, 'Услуга уже оказана. Повторного списания нет.\n' + intent['receipt']
                        if intent['status'] != 'pending' or intent['expired']:
                            return False, 'Подтверждение отменено или истекло. Выберите услугу заново.'
                        if cancel:
                            await con.execute("UPDATE max_service_intents SET status='cancelled' WHERE token=$1", token)
                            return True, 'Услуга отменена. Золото и прогресс не изменены.'
                        actor = db._row_to_char(row)
                        error = guard(ch)
                        if error:
                            return False, error
                        if actor.room != intent['room'] or ch.room != intent['room'] or _provider(actor, operation) != intent['provider']:
                            return False, 'Вы покинули мастера. Выберите услугу заново.'
                        snapshot = json.loads(intent['snapshot']) if isinstance(intent['snapshot'], str) else intent['snapshot']
                        if _snapshot(actor, operation) != snapshot:
                            return False, 'Снаряжение или износ изменились. Запросите новую цену ремонта.'
                        before = _snapshot(actor, operation)
                        learned, loadout = list(actor.learned), list(actor.loadout)
                        ok, receipt = _action(actor, operation, intent['subject'])
                        if not ok:
                            return False, receipt
                        price = int(row['gold']) - actor.gold
                        if price != intent['price']:
                            return False, 'Цена изменилась. Выберите услугу заново.'
                        patch = ({'durab': {slot: [item, DURAB_MAX-durab] for slot, (item, durab) in before.items()}}
                                 if operation == 'repair' else
                                 {'learned': [s for s in actor.learned if s not in learned],
                                  'loadout': [s for s in actor.loadout if s not in loadout]})
                        await con.execute('''UPDATE characters SET gold=$2,flags=$3,learned=$4,loadout=$5,
                            updated_at=now() WHERE uid=$1''', ch.uid, actor.gold, json.dumps(actor.flags),
                            json.dumps(actor.learned), json.dumps(actor.loadout))
                        for suffix, uid, delta in (('player', ch.uid, -price), ('sink', 0, price)):
                            await con.execute(LEDGER_SQL, f'maxservice:{token}:{suffix}', uid, operation,
                                              delta, intent['subject'] or None, None, time.time())
                        await con.execute("UPDATE max_service_intents SET status='done',receipt=$2 WHERE token=$1", token, receipt)
                        committing = (token, ch.generation, -price, patch)
                _apply(ch, -price, patch)
                return True, receipt
            except BaseException:
                if committing is not None:
                    if not hasattr(db, '_service_uncertain'):
                        db._service_uncertain = {}
                    db._service_uncertain[ch.uid] = committing
                raise
