"""Durable text delivery, independent of the game/input execution path."""
import asyncio
import logging
import uuid

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
                await con.executemany('''
                    INSERT INTO max_outbox(uid, external_user_id, message_text)
                    VALUES($1,$2,$3)
                ''', [(uid, external_user_id, part) for part in parts])
        return len(parts)

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
                            AND earlier.status IN ('pending','processing'))
                    ORDER BY o.id LIMIT 1 FOR UPDATE OF o SKIP LOCKED
                ''')
                if row is None:
                    return None
                row = dict(row)
                await con.execute('''
                    UPDATE max_outbox SET status='processing', lease_token=$2,
                        lease_until=now()+$3*interval '1 second', attempts=attempts+1
                    WHERE id=$1
                ''', row['id'], token, LEASE_SECONDS)
                row['lease_token'] = token
                row['attempts'] += 1
                row['expired'] = await con.fetchval(
                    'SELECT expires_at<=now() FROM max_outbox WHERE id=$1', row['id'])
                return row

    async def finish(self, row, status, error=None, delay=0):
        if status not in ('sent', 'failed', 'pending'):
            raise ValueError('Invalid outbox status')
        # Lease fencing prevents a stale sender from changing a recovered claim.
        return await self.pool.fetchval('''
            UPDATE max_outbox SET status=$3, last_error=$4,
                next_attempt_at=now()+$5*interval '1 second',
                lease_until=NULL, lease_token=NULL,
                message_text=CASE WHEN $3='pending' THEN message_text ELSE NULL END,
                finished_at=CASE WHEN $3='pending' THEN NULL ELSE now() END
            WHERE id=$1 AND lease_token=$2 AND status='processing'
            RETURNING id
        ''', row['id'], row['lease_token'], status, error, float(delay)) is not None

    async def cleanup(self):
        await self.pool.execute("DELETE FROM max_outbox WHERE finished_at<now()-interval '7 days'")


class MaxOutboxWorker:
    def __init__(self, store, client):
        self.store = store
        self.client = client

    async def process_one(self):
        row = await self.store.claim()
        if row is None:
            return False
        if row['expired'] or row['attempts'] > MAX_ATTEMPTS:
            await self.store.finish(row, 'failed', 'expired' if row['expired'] else 'attempts_exhausted')
            return True
        try:
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
