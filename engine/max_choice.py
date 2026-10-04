"""Durable two-step story choices for MAX; shared quest rules remain authoritative."""
import asyncio
import json
import uuid

from . import content, game_actions, quest
from .shop_purchase import TOKEN, ShopPurchaseStore, guard


def _apply(ch, qid, option):
    previous = quest.choice_made(ch, qid)
    if previous is not None and previous != option:
        raise RuntimeError('Conflicting cached story choice; reconciliation required')
    ch.flags.setdefault('quest_choices', {})[qid] = option


async def recover(db, ch):
    uncertain = getattr(db, '_choice_uncertain', {}).get(ch.uid)
    if uncertain is None:
        return
    token, generation, qid, option = uncertain
    async with db.pool.acquire() as con:
        async with con.transaction():
            row = await con.fetchrow('SELECT status FROM max_choice_intents WHERE token=$1 AND uid=$2 FOR UPDATE', token, ch.uid)
    if row is None:
        raise RuntimeError('Story choice outcome unavailable; snapshot save blocked')
    if row['status'] == 'done' and ch.generation == generation:
        _apply(ch, qid, option)
    db._choice_uncertain.pop(ch.uid, None)


class ChoiceStore:
    def __init__(self, db):
        self.db = db

    async def quote(self, ch, qid, option_id):
        if guard(ch):
            return False, 'Выбор недоступен в бою или после смерти.', None
        db = self.db
        async with db._character_write_locks.setdefault(ch.uid, asyncio.Lock()):
            await db._save_character(ch)
            async with db.pool.acquire() as con:
                async with con.transaction():
                    row = await con.fetchrow('SELECT * FROM characters WHERE uid=$1 FOR UPDATE', ch.uid)
                    ShopPurchaseStore._current(row, ch)
                    actor = db._row_to_char(row)
                    if guard(ch):
                        return False, 'Выбор недоступен в бою или после смерти.', None
                    ok, message = game_actions.quest_choose_here(actor, qid, option_id)
                    if not ok:
                        return False, message, None
                    option = quest.choose_option(qid, option_id)
                    token = uuid.uuid4().hex
                    await con.execute("UPDATE max_choice_intents SET status='cancelled' WHERE uid=$1 AND status='pending'", ch.uid)
                    await con.execute('''INSERT INTO max_choice_intents
                        (token,uid,generation,room,qid,option_id,option_snapshot)
                        VALUES($1,$2,$3,$4,$5,$6,$7)''', token, ch.uid, ch.generation, ch.room,
                        qid, option_id, json.dumps(option, ensure_ascii=False))
            return True, (f"🔀 {option.get('label', option_id)}\n{option.get('text', '').strip()}\n"
                          '⚠️ Выбор изменит путь героя. Подтвердите в течение 5 минут.\n'
                          'Новый вариант отменяет предыдущее подтверждение.'), token

    async def confirm(self, ch, token, cancel=False):
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            return False, 'Используйте кнопку подтверждения выбранного варианта или /confirm <токен>.'
        db = self.db
        async with db._character_write_locks.setdefault(ch.uid, asyncio.Lock()):
            await db._save_character(ch)
            committing = None
            try:
                async with db.pool.acquire() as con:
                    async with con.transaction():
                        row = await con.fetchrow('SELECT * FROM characters WHERE uid=$1 FOR UPDATE', ch.uid)
                        ShopPurchaseStore._current(row, ch)
                        intent = await con.fetchrow('''SELECT *, expires_at <= now() AS expired FROM
                            max_choice_intents WHERE token=$1 AND uid=$2 FOR UPDATE''', token, ch.uid)
                        if intent is None or intent['generation'] != ch.generation:
                            return False, 'Это подтверждение не принадлежит текущему герою.'
                        if intent['status'] == 'done':
                            return True, 'Выбор уже сохранён.\n' + intent['receipt']
                        if intent['status'] != 'pending' or intent['expired']:
                            return False, 'Подтверждение отменено или истекло. Выберите вариант заново.'
                        if cancel:
                            await con.execute("UPDATE max_choice_intents SET status='cancelled' WHERE token=$1", token)
                            return True, 'Выбор отменён. Путь героя не изменён.'
                        if guard(ch):
                            return False, 'Выбор недоступен в бою или после смерти.'
                        actor = db._row_to_char(row)
                        if ch.room != intent['room'] or actor.room != intent['room']:
                            return False, 'Вы покинули персонажа. Выберите вариант заново.'
                        snapshot = json.loads(intent['option_snapshot']) if isinstance(intent['option_snapshot'], str) else intent['option_snapshot']
                        if quest.choose_option(intent['qid'], intent['option_id']) != snapshot:
                            return False, 'Вариант изменился. Прочитайте новое предложение.'
                        ok, receipt = game_actions.quest_choose_here(actor, intent['qid'], intent['option_id'])
                        if not ok:
                            return False, receipt
                        await con.execute('UPDATE characters SET flags=$2,updated_at=now() WHERE uid=$1',
                                          ch.uid, json.dumps(actor.flags))
                        await con.execute("UPDATE max_choice_intents SET status='done',receipt=$2 WHERE token=$1", token, receipt)
                        committing = (token, ch.generation, intent['qid'], intent['option_id'])
                _apply(ch, intent['qid'], intent['option_id'])
                return True, receipt
            except BaseException:
                if committing is not None:
                    if not hasattr(db, '_choice_uncertain'):
                        db._choice_uncertain = {}
                    db._choice_uncertain[ch.uid] = committing
                raise
