"""Authoritative, row-locked guild roster and durable invitations.

Bank transfers lock the same guild rows. Never repair database permissions
from a potentially stale display cache.
"""
import time
import json
from contextlib import asynccontextmanager

from .guild import GuildManager, CREATE_COST
from . import guild_tx
from . import uigate


async def _load(conn):
    await conn.execute("SELECT pg_advisory_xact_lock(734921)")
    rows = await conn.fetch("SELECT gid, name, leader, bank_gold, bank_items, created "
                            "FROM guilds ORDER BY gid FOR UPDATE")
    members = await conn.fetch("SELECT uid, gid, rank, joined FROM guild_members ORDER BY uid")
    invites = await conn.fetch("SELECT uid, gid FROM guild_invites")
    mgr = GuildManager.__new__(GuildManager)
    mgr.db_mode = True
    mgr.guilds = {str(r['gid']): dict(name=r['name'], leader=int(r['leader']),
                 members=[], ranks={}, bank_gold=int(r['bank_gold']),
                 bank_items=guild_tx._inv(r['bank_items']), founded=r['created']) for r in rows}
    mgr.member_of = {}
    for row in members:
        uid, gid = int(row['uid']), str(row['gid'])
        if gid in mgr.guilds:
            mgr.member_of[uid] = gid
            mgr.guilds[gid]['members'].append(uid)
            mgr.guilds[gid]['ranks'][str(uid)] = row['rank']
    mgr.invites = {int(r['uid']): str(r['gid']) for r in invites if str(r['gid']) in mgr.guilds}
    mgr._next = max((int(g) for g in mgr.guilds if g.isdigit()), default=0) + 1
    counter = await conn.fetchrow("SELECT v FROM kv_state WHERE k=$1", 'guild_next_id')
    if counter:
        value = json.loads(counter['v']) if isinstance(counter['v'], str) else counter['v']
        mgr._next = max(mgr._next, int(value))
    return mgr


def snapshot(mgr):
    return mgr.guilds, mgr.member_of, mgr.invites, mgr._next


@asynccontextmanager
async def _existing_transaction():
    yield


async def change(cf, operation, required_uids=(), in_transaction=False):
    async with cf() as conn:
        async with (_existing_transaction() if in_transaction else conn.transaction()):
            # Same order as bank transfers: characters before guild rows.
            for uid in sorted(set(required_uids)):
                row = await conn.fetchrow("SELECT level, deleted_at FROM characters WHERE uid=$1 FOR UPDATE", uid)
                if row is None or row['deleted_at'] is not None:
                    raise ValueError('Персонаж больше не существует.')
                if not uigate.unlocked('guild', int(row['level'])):
                    raise ValueError('Гильдии доступны с 10-го уровня.')
            mgr = await _load(conn)
            before = {u: (g, mgr.rank(u)) for u, g in mgr.member_of.items()}
            leaders = {g: value['leader'] for g, value in mgr.guilds.items()}
            old_invites = dict(mgr.invites)
            active = {int(row['uid']) for row in await conn.fetch(
                "SELECT uid FROM characters WHERE deleted_at IS NULL")}
            for uid in list(mgr.member_of):
                if uid not in active:
                    mgr.leave(uid)
            mgr.invites = {u: g for u, g in mgr.invites.items() if u in active}
            result = await operation(mgr, conn)
            after = {u: (g, mgr.rank(u)) for u, g in mgr.member_of.items()}
            for uid in before.keys() - after.keys():
                await conn.execute("DELETE FROM guild_members WHERE uid=$1", uid)
            for uid, (gid, rank) in after.items():
                if before.get(uid) != (gid, rank):
                    await conn.execute("INSERT INTO guild_members (uid,gid,rank,joined) "
                                       "VALUES ($1,$2,$3,$4) ON CONFLICT (uid) DO UPDATE "
                                       "SET gid=EXCLUDED.gid, rank=EXCLUDED.rank, joined=EXCLUDED.joined",
                                       uid, gid, rank, time.time())
            for gid, leader in leaders.items():
                if gid not in mgr.guilds:
                    await conn.execute("DELETE FROM guilds WHERE gid=$1", gid)
                elif mgr.guilds[gid]['leader'] != leader:
                    await conn.execute("UPDATE guilds SET leader=$1 WHERE gid=$2",
                                       mgr.guilds[gid]['leader'], gid)
            mgr.invites = {u: g for u, g in mgr.invites.items()
                           if g in mgr.guilds and u not in mgr.member_of}
            for uid in old_invites.keys() - mgr.invites.keys():
                await conn.execute("DELETE FROM guild_invites WHERE uid=$1", uid)
            for uid, gid in mgr.invites.items():
                if old_invites.get(uid) != gid:
                    await conn.execute("INSERT INTO guild_invites (uid,gid) VALUES ($1,$2) "
                                       "ON CONFLICT (uid) DO UPDATE SET gid=EXCLUDED.gid", uid, gid)
            # Never reuse dissolved guild IDs: historical ledger references remain unique.
            await conn.execute("INSERT INTO kv_state (k,v,updated) VALUES ($1,$2,$3) "
                               "ON CONFLICT (k) DO UPDATE SET v=EXCLUDED.v, updated=EXCLUDED.updated",
                               'guild_next_id', json.dumps(mgr._next), time.time())
        return result, snapshot(mgr)  # only publish after COMMIT


async def action(cf, uid, op, target=None, name=None, op_id=None):
    async def apply(mgr, conn):
        if op == 'create':
            if mgr.guild_of(uid):
                return False, 'Вы уже в гильдии.'
            @asynccontextmanager
            async def same_connection():
                yield conn
            gid = str(mgr._next)
            ok, msg, gold = await guild_tx.create_guild(
                same_connection, gid, name, uid, CREATE_COST, op_id)
            if ok and gold is not None:
                mgr.create(uid, name)
            return ok, msg, gold
        if op == 'invite':
            ok = mgr.invite(uid, target)
            return ok, 'Приглашение отправлено.' if ok else 'Нет прав или игрок уже в гильдии.'
        if op == 'accept':
            ok = mgr.accept(uid) is not None
            return ok, 'Вы вступили в гильдию.' if ok else 'Приглашение истекло или вы уже в гильдии.'
        if op == 'decline':
            mgr.decline(uid)
            return True, 'Приглашение отклонено.'
        if op == 'leave':
            g = mgr.guild_of(uid)
            if g and len(g['members']) == 1 and (g['bank_gold'] or g['bank_items']):
                return False, 'Перед роспуском последней гильдии заберите золото и предметы из банка.'
            ok = mgr.leave(uid) is not None
            return ok, 'Вы вышли из гильдии.' if ok else 'Вы не в гильдии.'
        if op in ('kick', 'promote', 'demote'):
            ok = getattr(mgr, op)(uid, target)
            return ok, 'Состав гильдии обновлён.' if ok else 'Недостаточно прав или действие устарело.'
        return False, 'Неизвестное действие.'
    return await change(cf, apply, (uid, target) if op == 'invite' else (uid,))


async def load(cf):
    async def read(mgr, conn):
        return True
    _, state = await change(cf, read)
    return state


async def remove_player(conn, uid):
    """Called inside character reset; cleanup rolls back with the reset."""
    @asynccontextmanager
    async def same_connection():
        yield conn
    async def remove(mgr, connection):
        mgr.leave(uid)
        mgr.decline(uid)
    await change(same_connection, remove, in_transaction=True)
