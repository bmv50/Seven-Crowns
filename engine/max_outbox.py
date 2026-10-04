"""Durable text delivery, independent of the game/input execution path."""
import asyncio
import logging
import uuid
import time
from datetime import datetime, timezone

from aiohttp import ClientError


MAX_ATTEMPTS = 8
LEASE_SECONDS = 60
_log = logging.getLogger(__name__)


class MaxSendError(Exception):
    """Sanitized HTTP failure; deliberately excludes body, URL and headers."""
    def __init__(self, status, retry_after=0):
        self.status = status
        self.retry_after = retry_after
        self.retryable = status in (408, 425, 429) or status >= 500
        super().__init__(f'MAX HTTP {status}')


def text_parts(value):
    text = str(value).replace('*', '')
    return [text[i:i + 3500] for i in range(0, len(text), 3500)]


class MaxOutboxStore:
    def __init__(self, pool):
        if pool is None:
            raise RuntimeError('MAX outbox requires PostgreSQL')
        self.pool = pool

    async def enqueue(self, uid, external_user_id, value):
        from .identity import identity_key
        _, external_user_id = identity_key('max', external_user_id)
        if type(uid) is not int or uid >= 0:
            raise ValueError('MAX internal uid must be negative')
        parts = text_parts(value)
        if not parts:
            return 0
        async with self.pool.acquire() as con:
            async with con.transaction():
                # Keep all chunks of concurrent replies contiguous in this dialog.
                await con.execute('SELECT pg_advisory_xact_lock(734925, hashtext($1))',
                                  external_user_id)
                # A command/result is a boundary: close and release prior summaries.
                await con.execute('''UPDATE max_outbox SET combat_open=FALSE,
                    next_attempt_at=least(next_attempt_at,now())
                    WHERE external_user_id=$1 AND combat_open AND status='pending' AND attempts=0
                ''', external_user_id)
                await con.executemany('''
                    INSERT INTO max_outbox(uid, external_user_id, message_text)
                    VALUES($1,$2,$3)
                ''', [(uid, external_user_id, part) for part in parts])
        return len(parts)

    async def enqueue_combat(self, uid, external_user_id, value, snapshot, battle_key,
                             generation, urgent=False):
        from .identity import identity_key
        from . import max_combat
        _, external_user_id = identity_key('max', external_user_id)
        if type(uid) is not int or uid >= 0 or not battle_key or not isinstance(snapshot, str):
            raise ValueError('Invalid MAX combat summary')
        log = str(value).replace('*', '')
        budget = 3500 - len(max_combat.HEADER) - len(snapshot) - 2
        if budget < 1:
            raise ValueError('MAX combat snapshot is too long')
        if not log:
            return False
        async with self.pool.acquire() as con:
            async with con.transaction():
                await con.execute('SELECT pg_advisory_xact_lock(734925, hashtext($1))', external_user_id)
                current = await con.fetchval('''SELECT EXISTS (
                    SELECT 1 FROM characters c JOIN platform_identities i ON i.uid=c.uid
                    WHERE c.uid=$1 AND c.generation=$2 AND c.deleted_at IS NULL
                    AND i.platform='max' AND i.external_user_id=$3)
                ''', uid, generation, external_user_id)
                if not current:
                    return False
                await con.execute('''UPDATE max_outbox SET combat_open=FALSE, next_attempt_at=now()
                    WHERE external_user_id=$1 AND combat_open AND status='pending' AND attempts=0
                      AND (combat_key<>$2 OR generation<>$3)
                ''', external_user_id, battle_key, generation)
                pending = await con.fetchrow('''SELECT id, combat_log FROM max_outbox
                    WHERE external_user_id=$1 AND combat_key=$2 AND generation=$3
                    AND combat_open AND status='pending' AND attempts=0 AND expires_at>now()
                    ORDER BY id DESC LIMIT 1 FOR UPDATE
                ''', external_user_id, battle_key, generation)
                combined = pending['combat_log'] + '\n' + log if pending else log
                if pending and len(combined) <= budget:
                    await con.execute('''UPDATE max_outbox SET combat_log=$2, combat_snapshot=$3,
                        message_text=$4, combat_open=NOT $5,
                        next_attempt_at=CASE WHEN $5 THEN now() ELSE next_attempt_at END
                        WHERE id=$1
                    ''', pending['id'], combined, snapshot, max_combat.render(combined, snapshot), urgent)
                    return True
                if pending:
                    # Preserve the earlier chunk in full, rather than truncating old hits.
                    await con.execute("UPDATE max_outbox SET combat_open=FALSE, next_attempt_at=now() WHERE id=$1",
                                      pending['id'])
                parts = [log[i:i+budget] for i in range(0, len(log), budget)]
                for index, part in enumerate(parts):
                    open_batch = not urgent and index == len(parts)-1
                    await con.execute('''INSERT INTO max_outbox(uid,external_user_id,message_text,
                        generation,combat_key,combat_open,combat_log,combat_snapshot,next_attempt_at,expires_at)
                        VALUES($1,$2,$3,$4,$5,$6,$7,$8,
                               now()+$9*interval '1 second',now()+$10*interval '1 second')
                    ''', uid, external_user_id, max_combat.render(part, snapshot), generation,
                        battle_key, open_batch, part, snapshot,
                        float(max_combat.WINDOW_SECONDS if open_batch else 0), float(max_combat.TTL_SECONDS))
        return True

    async def combat_current(self, row):
        return await self.pool.fetchval('''SELECT EXISTS (
            SELECT 1 FROM characters WHERE uid=$1 AND generation=$2 AND deleted_at IS NULL)
        ''', row['uid'], row['generation'])

    async def claim(self):
        token = uuid.uuid4().hex
        async with self.pool.acquire() as con:
            async with con.transaction():
                row = await con.fetchrow('''
                    SELECT o.* FROM max_outbox o
                    WHERE (o.status='pending' AND o.next_attempt_at<=now()
                           OR o.status='processing' AND o.lease_until<=now())
                      AND NOT EXISTS (
                          SELECT 1 FROM max_outbox earlier
                          WHERE earlier.external_user_id=o.external_user_id
                            AND earlier.id<o.id
                            AND earlier.status IN ('pending','processing')
                            AND (o.category IS NOT NULL OR earlier.category IS NULL))
                    ORDER BY (o.category IS NOT NULL), o.id LIMIT 1 FOR UPDATE OF o SKIP LOCKED
                ''')
                if row is None:
                    return None
                row = dict(row)
                await con.execute('''
                    UPDATE max_outbox SET status='processing', lease_token=$2,
                        lease_until=now()+$3*interval '1 second', attempts=attempts+1, combat_open=FALSE
                    WHERE id=$1
                ''', row['id'], token, LEASE_SECONDS)
                row['lease_token'] = token
                row['attempts'] += 1
                row['expired'] = await con.fetchval(
                    'SELECT expires_at<=now() FROM max_outbox WHERE id=$1', row['id'])
                return row

    async def enqueue_notification(self, uid, external_user_id, value, category,
                                   generation, expires_at=None, event_key=None):
        from . import notify
        from .identity import identity_key
        _, external_user_id = identity_key('max', external_user_id)
        if type(uid) is not int or uid >= 0 or category not in notify.CATEGORIES:
            raise ValueError('Invalid MAX notification')
        deadline = min(expires_at, time.time() + 86400) if expires_at is not None else time.time() + 86400
        text = str(value).replace('*', '')
        if len(text) > 3500:
            suffix = '\n… Сообщение сокращено.'
            text = text[:3500-len(suffix)] + suffix
        if not text or deadline <= time.time():
            return False
        key = f'{uid}:{generation}:{event_key}' if event_key is not None else None
        return await self.pool.fetchval('''
            INSERT INTO max_outbox(uid, external_user_id, message_text, category, generation,
                                   expires_at, dedup_key)
            SELECT $1,$2,$3,$4,$5,$6,$7 FROM characters c
            WHERE c.uid=$1 AND c.generation=$5 AND c.deleted_at IS NULL
              AND NOT c.notify_blocked AND c.flags->'notify'->>'push_enabled'='true'
              AND COALESCE(c.flags->'notify'->>$4,'true')='true'
              AND EXISTS (SELECT 1 FROM platform_identities i
                          WHERE i.platform='max' AND i.uid=c.uid AND i.external_user_id=$2)
            ON CONFLICT DO NOTHING RETURNING id
        ''', uid, external_user_id, text, category, generation,
            datetime.fromtimestamp(deadline, timezone.utc), key) is not None

    async def finish(self, row, status, error=None, delay=0, *, con=None, attempted=True):
        if status not in ('sent', 'failed', 'pending'):
            raise ValueError('Invalid outbox status')
        # Lease fencing prevents a stale sender from changing a recovered claim.
        return await (con or self.pool).fetchval('''
            UPDATE max_outbox SET status=$3, last_error=$4,
                next_attempt_at=now()+$5*interval '1 second',
                lease_until=NULL, lease_token=NULL,
                attempts=CASE WHEN $6 THEN attempts ELSE greatest(0,attempts-1) END,
                message_text=CASE WHEN $3='pending' THEN message_text ELSE NULL END,
                combat_log=CASE WHEN $3='pending' THEN combat_log ELSE NULL END,
                combat_snapshot=CASE WHEN $3='pending' THEN combat_snapshot ELSE NULL END,
                finished_at=CASE WHEN $3='pending' THEN NULL ELSE now() END
            WHERE id=$1 AND lease_token=$2 AND status='processing'
            RETURNING id
        ''', row['id'], row['lease_token'], status, error, float(delay), attempted) is not None

    async def cleanup(self):
        await self.pool.execute("DELETE FROM max_outbox WHERE finished_at<now()-interval '7 days'")


class MaxOutboxWorker:
    def __init__(self, store, client, notification_sender=None):
        self.store = store
        self.client = client
        self.notification_sender = notification_sender

    async def process_one(self):
        row = await self.store.claim()
        if row is None:
            return False
        if row['expired'] or row['attempts'] > MAX_ATTEMPTS:
            await self.store.finish(row, 'failed', 'expired' if row['expired'] else 'attempts_exhausted')
            return True
        if row.get('combat_key') and not await self.store.combat_current(row):
            await self.store.finish(row, 'failed', 'stale_combat')
            return True
        try:
            if row.get('category'):
                if self.notification_sender is None:
                    await self.store.finish(row, 'failed', 'notification_policy_missing')
                else:
                    await self.notification_sender.deliver(row)
                return True
            # Exactly one HTTP chunk: successful earlier chunks are never replayed.
            await self.client.send_chunk(row['external_user_id'], row['message_text'])
        except asyncio.CancelledError:
            # Leave the lease for restart recovery; HTTP delivery may be uncertain.
            raise
        except Exception as exc:
            retryable = isinstance(exc, (ClientError, TimeoutError))
            delay = min(300, 2 ** row['attempts'])
            error = 'transport_error'
            if isinstance(exc, MaxSendError):
                retryable = exc.retryable
                delay = max(delay, exc.retry_after)
                error = f'http_{exc.status}'
            elif not retryable:
                if row.get('category'):
                    raise  # DB/policy failure: retain lease; watchdog will recover.
                error = 'unexpected_error'
            status = 'pending' if retryable and row['attempts'] < MAX_ATTEMPTS else 'failed'
            await self.store.finish(row, status, error, delay)
            # Never log exception bodies/headers, which can contain credentials.
            _log.warning('MAX delivery %s: id=%s error=%s attempt=%s',
                         status, row['id'], error, row['attempts'])
        else:
            await self.store.finish(row, 'sent')
        return True

    async def run(self):
        cleanup_at = 0
        while True:
            now = asyncio.get_running_loop().time()
            if now >= cleanup_at:
                await self.store.cleanup()
                cleanup_at = now + 3600
            worked = await self.process_one()
            # Global request spacing, in addition to MaxClient's dialog throttle.
            await asyncio.sleep(0.05 if worked else 0.25)
