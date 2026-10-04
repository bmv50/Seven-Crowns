"""Atomic party snapshots in the existing PostgreSQL runtime-state table."""

import json
import time

from .social import PartyManager

STATE_KEY = "parties"


async def change(acquire, operation, valid_uids=None, required_uids=None):
    """Read, validate and change the latest snapshot under a PostgreSQL row lock.

    The caller updates its live manager only after this transaction commits.
    Every operation reloads PostgreSQL, so an uncertain prior commit is not
    overwritten by an older in-memory snapshot.
    """
    async with acquire() as con:
        async with con.transaction():
            await con.execute(
                "INSERT INTO kv_state (k,v,updated) VALUES ($1,$2,$3) "
                "ON CONFLICT (k) DO NOTHING",
                STATE_KEY, json.dumps(PartyManager().export_state()), time.time())
            value = await con.fetchval("SELECT v FROM kv_state WHERE k=$1 FOR UPDATE", STATE_KEY)
            state = json.loads(value) if isinstance(value, (str, bytes)) else value
            manager = PartyManager()
            if valid_uids is not None:
                active = await con.fetch("SELECT uid FROM characters WHERE deleted_at IS NULL")
                valid_uids = valid_uids & {int(row["uid"]) for row in active}
                if required_uids and not set(required_uids) <= valid_uids:
                    raise ValueError("Party action requires active characters")
            manager.import_state(state, valid_uids)
            result = operation(manager)
            snapshot = manager.export_state()
            await con.execute("UPDATE kv_state SET v=$2, updated=$3 WHERE k=$1",
                              STATE_KEY, json.dumps(snapshot), time.time())
    return result, snapshot


async def load(acquire, valid_uids=None):
    _, snapshot = await change(acquire, lambda manager: None, valid_uids)
    return snapshot


async def remove_player(con, uid):
    """Remove membership in the same transaction as character reset."""
    row = await con.fetchrow("SELECT v FROM kv_state WHERE k=$1 FOR UPDATE", STATE_KEY)
    if row is None:
        return
    state = json.loads(row["v"]) if isinstance(row["v"], (str, bytes)) else row["v"]
    manager = PartyManager()
    manager.import_state(state)
    manager.leave(uid)
    manager.invites.pop(uid, None)
    await con.execute("UPDATE kv_state SET v=$2, updated=$3 WHERE k=$1",
                      STATE_KEY, json.dumps(manager.export_state()), time.time())
