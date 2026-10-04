"""MAX push policy and durable post-ACK quota, serialized with character saves."""
import asyncio
import json
import logging
import time
from types import SimpleNamespace

from . import notify
from .max_outbox import MaxSendError

_log = logging.getLogger(__name__)


class MaxNotificationSender:
    def __init__(self, db, store, client, get_char, on_sent=None, clock=time.time):
        self.db, self.store, self.client = db, store, client
        self.get_char, self.on_sent, self.clock = get_char, on_sent, clock

    async def deliver(self, row):
        uid, category = row['uid'], row['category']
        quota = None
        sent = False
        async with self.db._character_write_locks.setdefault(uid, asyncio.Lock()):
            async with self.db.pool.acquire() as con:
                async with con.transaction():
                    character = await con.fetchrow('''
                        SELECT flags, generation, deleted_at, notify_blocked
                        FROM characters WHERE uid=$1 FOR UPDATE
                    ''', uid)
                    # Fence AND lock the lease before HTTP, including against recovery.
                    leased = await con.fetchrow('''
                        SELECT expires_at FROM max_outbox WHERE id=$1
                        AND status='processing' AND lease_token=$2 FOR UPDATE
                    ''', row['id'], row['lease_token'])
                    if leased is None:
                        return 'stale'
                    now = self.clock()
                    if (not notify.ENABLED or character is None or character['deleted_at'] is not None
                            or character['generation'] != row['generation'] or character['notify_blocked']
                            or leased['expires_at'].timestamp() <= now):
                        await self.store.finish(row, 'failed', 'notification_ineligible', con=con, attempted=False)
                        return 'drop'
                    flags = character['flags']
                    actor = SimpleNamespace(flags=json.loads(flags) if isinstance(flags, str) else dict(flags))
                    verdict = notify.allow(actor, category, now)
                    if verdict == 'defer':
                        morning = notify.next_morning(now, actor)
                        if morning < leased['expires_at'].timestamp():
                            await self.store.finish(row, 'pending', 'quiet_hours', morning-now,
                                                    con=con, attempted=False)
                            return 'defer'
                        verdict = 'drop'
                    if verdict != 'send':
                        await self.store.finish(row, 'failed', 'notification_policy', con=con, attempted=False)
                        return 'drop'
                    try:
                        await self.client.send_chunk(row['external_user_id'], row['message_text'])
                    except MaxSendError as exc:
                        if exc.status != 403:
                            raise
                        await con.execute('UPDATE characters SET notify_blocked=TRUE WHERE uid=$1', uid)
                        await con.execute('INSERT INTO notify_log(uid,category,ts,ok) VALUES($1,$2,$3,FALSE)',
                                          uid, category, self.clock())
                        await self.store.finish(row, 'failed', 'http_403', con=con)
                        return 'blocked'
                    # ACK, quota and delivery journal commit together. Queueing is not sending.
                    notify.record_sent(actor, category, self.clock())
                    quota = actor.flags.get('notify_quota')
                    if category not in notify._OFF_QUOTA:
                        await con.execute('''UPDATE characters SET flags=jsonb_set(flags,
                            '{notify_quota}', $2::jsonb, TRUE) WHERE uid=$1''', uid, json.dumps(quota))
                    await con.execute('INSERT INTO notify_log(uid,category,ts,ok) VALUES($1,$2,$3,TRUE)',
                                      uid, category, self.clock())
                    if not await self.store.finish(row, 'sent', con=con):
                        raise RuntimeError('Lost MAX notification lease')
                    sent = True
            # Publish only after COMMIT, while save/settings are still serialized.
            ch = self.get_char(uid)
            if sent and ch is not None and ch.generation == row['generation'] and quota is not None:
                ch.flags['notify_quota'] = dict(quota)
        if sent and self.on_sent:
            try:
                self.on_sent(row)
            except Exception:
                _log.warning('MAX notification telemetry failed: id=%s', row['id'])
        return 'sent'
