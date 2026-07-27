# -*- coding: utf-8 -*-
"""
Тесты атомарного жизненного цикла персонажа с защитой поколением (Аудит-2а.1) —
БЕЗ Postgres.

В файле собственная мок-СУБД (FakeConn/FakePool по образцу test_econ_tx.py):
таблица characters — dict uid -> строка (все колонки; equipment/inventory/quests/
flags хранятся JSON-строками, как отдаёт asyncpg JSONB); audit_log — список.
transaction() — контекст-менеджер со снимком: при исключении откат к снимку
(доказывает, что провал audit/UPDATE не оставляет полузаписи). FOR UPDATE в
одном потоке — no-op, идемпотентность обеспечивают проверки generation/deleted_at.

Тестируется РЕАЛЬНЫЙ код engine/db.py: Database с подменённым self.pool.
Проверяется: create (gen 1 / поверх удалённого gen+1 / активный → отказ),
reset (поднятие поколения, инвалидация stale), restore (окно 24ч), stale-save
(0 строк → StaleCharacterWrite), «переживание рестарта» (load_all), откат
транзакции при ошибке audit/БД, идемпотентность двойных кликов.
"""
import asyncio
import copy
import json
import sys
import time

from engine.character import Character
from engine.db import Database
from engine.lifecycle_errors import (
    ActiveCharacterExists, StaleCharacterWrite, CharacterNotFound, RestoreExpired)


# ─────────────────────────── мок-СУБД ───────────────────────────
class _Tx:
    """Контекст транзакции: снимок на входе, откат при исключении/сбое коммита."""
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        self.conn._snapshot = self.conn._snap()
        return self

    async def __aexit__(self, et, ev, tb):
        if et is not None:                 # исключение в теле → откат
            self.conn._restore(self.conn._snapshot)
            return False
        if self.conn.fail_commit:
            self.conn._restore(self.conn._snapshot)
            raise RuntimeError("commit failed")
        return False


def _row_from_args(a):
    """Строка characters из 15 аргументов INSERT/UPDATE-замены (uid..generation)."""
    return {"uid": int(a[0]), "name": a[1], "cls": a[2], "race": a[3], "room": a[4],
            "level": int(a[5]), "xp": int(a[6]), "hp": int(a[7]), "mp": int(a[8]),
            "gold": int(a[9]), "equipment": a[10], "inventory": a[11],
            "quests": a[12], "flags": a[13], "generation": int(a[14]),
            "deleted_at": None}


class FakeConn:
    def __init__(self, characters=None):
        # characters: uid -> строка-словарь (см. _row_from_args)
        self.characters = characters or {}
        self.audit = []                    # audit_log: список dict-ов
        self.fail_on_execute = None        # подстрока SQL → одноразовый сбой
        self.fail_commit = False
        self._snapshot = None

    # — снимок/откат —
    def _snap(self):
        return (copy.deepcopy(self.characters), copy.deepcopy(self.audit))

    def _restore(self, snap):
        self.characters, self.audit = copy.deepcopy(snap[0]), copy.deepcopy(snap[1])

    def transaction(self):
        return _Tx(self)

    # — исполнение SQL (сопоставление по подстрокам) —
    async def execute(self, sql, *args):
        if self.fail_on_execute and self.fail_on_execute in sql:
            self.fail_on_execute = None
            raise RuntimeError("injected failure")

        if "audit_log" in sql:                                    # INSERT audit
            ts, uid, action, details = args
            self.audit.append({"ts": ts, "uid": int(uid), "action": action,
                               "details": json.loads(details)})
            return "INSERT 0 1"

        if "INSERT INTO characters" in sql:                       # новый персонаж
            self.characters[int(args[0])] = _row_from_args(args)
            return "INSERT 0 1"

        # save(): UPDATE строки СВОЕГО поколения активной записи
        if "AND generation=$15 AND deleted_at IS NULL" in sql:
            uid, gen = int(args[0]), int(args[14])
            row = self.characters.get(uid)
            if row is None or row["deleted_at"] is not None or int(row["generation"]) != gen:
                return "UPDATE 0"
            row.update(_row_from_args(args))
            row["deleted_at"] = None
            return "UPDATE 1"

        # create_character(): замена поверх удалённого (gen+1, deleted_at=NULL)
        if "generation=$15, deleted_at=NULL" in sql:
            self.characters[int(args[0])] = _row_from_args(args)
            return "UPDATE 1"

        # reset_character(): поднять поколение и мягко удалить
        if "generation=$2, deleted_at=now()" in sql:
            uid, new_gen = int(args[0]), int(args[1])
            self.characters[uid]["generation"] = new_gen
            self.characters[uid]["deleted_at"] = time.time()
            return "UPDATE 1"

        # restore_character(): снять мягкое удаление
        if "SET deleted_at=NULL WHERE uid=$1" in sql:
            self.characters[int(args[0])]["deleted_at"] = None
            return "UPDATE 1"

        raise AssertionError("FakeConn.execute: неизвестный SQL:\n" + sql)

    async def fetchrow(self, sql, *args):
        # окно восстановления: свежесть мягкого удаления
        if "deleted_at > now()" in sql:
            uid, max_age = int(args[0]), int(args[1])
            row = self.characters.get(uid)
            if row and row["deleted_at"] is not None and \
                    (time.time() - row["deleted_at"]) < max_age:
                return {"ok": 1}
            return None

        if "SELECT generation, deleted_at FROM characters" in sql:  # create/reset guard
            row = self.characters.get(int(args[0]))
            if row is None:
                return None
            return {"generation": int(row["generation"]), "deleted_at": row["deleted_at"]}

        if "SELECT * FROM characters WHERE uid=$1 FOR UPDATE" in sql:  # restore
            row = self.characters.get(int(args[0]))
            return dict(row) if row is not None else None

        raise AssertionError("FakeConn.fetchrow: неизвестный SQL:\n" + sql)

    async def fetch(self, sql, *args):
        if "WHERE deleted_at IS NULL" in sql:                     # load_all
            return [dict(r) for r in self.characters.values()
                    if r["deleted_at"] is None]
        raise AssertionError("FakeConn.fetch: неизвестный SQL:\n" + sql)


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *a):
        return False


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


def build():
    """Database с подменённым пулом (реальный код db.py, фейковое соединение)."""
    conn = FakeConn()
    db = Database()
    db.pool = FakePool(conn)
    return db, conn


def mkchar(uid=100, name="Герой", cls="warrior", race="human"):
    ch = Character(uid=uid, name=name, cls=cls, race=race)
    ch.gold = 3000
    ch.inventory = ["малое_зелье"]
    return ch


# ─────────────────────────── харнесс ───────────────────────────
_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print("  ❌ FAIL:", label)


async def raises(coro, exc):
    """True, если корутина бросила исключение типа exc."""
    try:
        await coro
        return False
    except exc:
        return True
    except Exception:
        return False


# ─────────────────────────── сценарии ───────────────────────────
async def scenario_create_fresh():
    """Создание без строки → generation 1; строка и audit('create') появились."""
    db, conn = build()
    ch = mkchar()
    gen = await db.create_character(ch)
    check(gen == 1, "create: возвращён generation 1")
    check(ch.generation == 1, "create: ch.generation проставлен в 1")
    check(conn.characters[100]["deleted_at"] is None, "create: строка активна (deleted_at NULL)")
    check(conn.characters[100]["generation"] == 1, "create: в БД generation 1")
    check(any(a["action"] == "create" and a["details"]["generation"] == 1
              for a in conn.audit), "create: audit('create') записан")


async def scenario_create_second_active():
    """Второй активный create того же uid → ActiveCharacterExists, БД не тронута."""
    db, conn = build()
    await db.create_character(mkchar())
    before = copy.deepcopy(conn.characters)
    ch2 = mkchar(name="Двойник")
    check(await raises(db.create_character(ch2), ActiveCharacterExists),
          "double-create: второй активный → ActiveCharacterExists")
    check(conn.characters == before, "double-create: строка не перезаписана")
    check(conn.characters[100]["name"] == "Герой", "double-create: имя прежнего сохранено")


async def scenario_reset_then_create_survives_restart():
    """reset → create нового → «рестарт» (load_all) видит нового, gen+1, active."""
    db, conn = build()
    ch = mkchar()
    await db.create_character(ch)                     # gen 1
    new_gen = await db.reset_character(100, ch.generation,
                                       {"name": ch.name, "level": ch.level})
    check(new_gen == 2, "reset: поколение поднято до 2")
    check(conn.characters[100]["deleted_at"] is not None, "reset: строка мягко удалена")
    ch2 = mkchar(name="Новый")
    gen2 = await db.create_character(ch2)             # поверх удалённого → gen 3
    check(gen2 == 3, "create-поверх-удалённого: generation 3 (не 1)")
    check(conn.characters[100]["deleted_at"] is None, "create-поверх: строка снова активна")
    # «рестарт»: перечитать мок как при старте бота
    loaded = await db.load_all()
    check(100 in loaded, "restart: новый герой поднят load_all")
    check(loaded[100].name == "Новый", "restart: это именно новый герой")
    check(loaded[100].generation == 3, "restart: generation пережил рестарт (=3)")


async def scenario_reset_then_restore():
    """reset → restore в окне: строка снова активна, поколение НЕ меняется."""
    db, conn = build()
    ch = mkchar()
    await db.create_character(ch)                     # gen 1
    await db.reset_character(100, 1, {})              # gen 2, deleted
    restored = await db.restore_character(100)
    check(restored is not None and restored.name == "Герой", "restore: вернулся тот же герой")
    check(restored.generation == 2, "restore: generation не меняется (=2)")
    check(conn.characters[100]["deleted_at"] is None, "restore: строка активна")
    check(any(a["action"] == "restore" for a in conn.audit), "restore: audit('restore') записан")
    loaded = await db.load_all()
    check(100 in loaded and loaded[100].generation == 2, "restore: load_all видит восстановленного")


async def scenario_restore_expired():
    """Истёкшее окно 24ч → RestoreExpired, строка остаётся удалённой."""
    db, conn = build()
    await db.create_character(mkchar())
    await db.reset_character(100, 1, {})
    # состарить мягкое удаление на ~28 часов
    conn.characters[100]["deleted_at"] = time.time() - 100000
    check(await raises(db.restore_character(100), RestoreExpired),
          "restore-expired: окно истекло → RestoreExpired")
    check(conn.characters[100]["deleted_at"] is not None,
          "restore-expired: строка осталась удалённой")
    loaded = await db.load_all()
    check(100 not in loaded, "restore-expired: истёкший герой не поднимается load_all")


async def scenario_double_reset():
    """Повторный reset → StaleCharacterWrite; reset несуществующего → CharacterNotFound."""
    db, conn = build()
    await db.create_character(mkchar())
    await db.reset_character(100, 1, {})              # gen 2, deleted
    check(await raises(db.reset_character(100, 1, {}), StaleCharacterWrite),
          "double-reset: повтор (уже удалён) → StaleCharacterWrite")
    check(await raises(db.reset_character(100, 2, {}), StaleCharacterWrite),
          "double-reset: даже с верным gen, но deleted → StaleCharacterWrite")
    check(await raises(db.reset_character(999, 1, {}), CharacterNotFound),
          "reset-missing: строки нет → CharacterNotFound")


async def scenario_double_restore():
    """Повторное восстановление уже активного → ActiveCharacterExists."""
    db, conn = build()
    await db.create_character(mkchar())
    await db.reset_character(100, 1, {})
    await db.restore_character(100)
    check(await raises(db.restore_character(100), ActiveCharacterExists),
          "double-restore: уже активен → ActiveCharacterExists")
    check(await raises(db.restore_character(777), CharacterNotFound),
          "restore-missing: строки нет → CharacterNotFound")


async def scenario_stale_save_after_reset():
    """Stale-объект (старое поколение) не может save() после reset (0 строк)."""
    db, conn = build()
    ch = mkchar()
    await db.create_character(ch)                     # ch.generation == 1
    await db.reset_character(100, 1, {})              # в БД gen 2, deleted
    ch.gold = 999999                                  # игрок «наиграл» на протухшем объекте
    check(await raises(db.save(ch), StaleCharacterWrite),
          "stale-save: save() устаревшего объекта → StaleCharacterWrite")
    check(conn.characters[100]["gold"] == 3000, "stale-save: золото в БД не затёрто")


async def scenario_stale_not_clobber_restored():
    """Stale-объект не затирает ВОССТАНОВЛЕННОГО героя."""
    db, conn = build()
    ch_old = mkchar()
    await db.create_character(ch_old)                 # gen 1
    await db.reset_character(100, 1, {})              # gen 2, deleted
    await db.restore_character(100)                   # активен, gen 2
    ch_old.room = "бездна"
    check(await raises(db.save(ch_old), StaleCharacterWrite),
          "stale-vs-restored: старый объект → StaleCharacterWrite")
    check(conn.characters[100]["room"] != "бездна",
          "stale-vs-restored: комната восстановленного не затёрта")
    check(conn.characters[100]["generation"] == 2, "stale-vs-restored: поколение 2 цело")


async def scenario_stale_not_clobber_new():
    """Stale-объект не затирает НОВОГО героя."""
    db, conn = build()
    ch_old = mkchar()
    await db.create_character(ch_old)                 # gen 1
    await db.reset_character(100, 1, {})              # gen 2, deleted
    ch_new = mkchar(name="Свежий")
    await db.create_character(ch_new)                 # gen 3, активен
    ch_old.gold = 1
    check(await raises(db.save(ch_old), StaleCharacterWrite),
          "stale-vs-new: старый объект → StaleCharacterWrite")
    check(conn.characters[100]["name"] == "Свежий", "stale-vs-new: имя нового не затёрто")
    # у нового объекта поколение верное — его save проходит
    ch_new.gold = 4242
    await db.save(ch_new)
    check(conn.characters[100]["gold"] == 4242, "stale-vs-new: save() нового героя работает")


async def scenario_audit_failure_rolls_back_reset():
    """Ошибка на записи audit откатывает ВСЮ транзакцию reset (атомарность)."""
    db, conn = build()
    await db.create_character(mkchar())              # gen 1
    conn.fail_on_execute = "audit_log"               # одноразовый сбой на audit
    raised = await raises(db.reset_character(100, 1, {}), Exception)
    check(raised, "audit-rollback: reset упал (проброс ошибки audit)")
    check(conn.characters[100]["generation"] == 1, "audit-rollback: поколение НЕ поднялось (откат)")
    check(conn.characters[100]["deleted_at"] is None, "audit-rollback: строка НЕ удалена (откат)")


async def scenario_audit_failure_rolls_back_restore():
    """Ошибка audit откатывает restore: строка остаётся удалённой."""
    db, conn = build()
    await db.create_character(mkchar())
    await db.reset_character(100, 1, {})
    conn.fail_on_execute = "audit_log"
    raised = await raises(db.restore_character(100), Exception)
    check(raised, "audit-rollback-restore: restore упал")
    check(conn.characters[100]["deleted_at"] is not None,
          "audit-rollback-restore: строка осталась удалённой (откат deleted_at)")


async def scenario_db_error_no_change():
    """Ошибка БД на UPDATE reset → откат: мок-таблица без изменений."""
    db, conn = build()
    await db.create_character(mkchar())
    conn.fail_on_execute = "generation=$2, deleted_at=now()"   # сбой на самом UPDATE reset
    before = copy.deepcopy(conn.characters)
    raised = await raises(db.reset_character(100, 1, {}), Exception)
    check(raised, "db-error: reset упал на UPDATE")
    check(conn.characters == before, "db-error: строки без изменений (откат)")
    check(conn.audit == [] or all(a["action"] != "reset" for a in conn.audit),
          "db-error: audit('reset') не записан")


async def scenario_pool_none_dev():
    """Без пула (dev): create → gen 1 в памяти; reset → expected+1; без падений."""
    db = Database()
    db.pool = None
    ch = mkchar()
    gen = await db.create_character(ch)
    check(gen == 1 and ch.generation == 1, "pool=None: create → generation 1 (память)")
    new_gen = await db.reset_character(100, 1, {})
    check(new_gen == 2, "pool=None: reset → expected+1 без БД")


async def run_all():
    for fn in (scenario_create_fresh,
               scenario_create_second_active,
               scenario_reset_then_create_survives_restart,
               scenario_reset_then_restore,
               scenario_restore_expired,
               scenario_double_reset,
               scenario_double_restore,
               scenario_stale_save_after_reset,
               scenario_stale_not_clobber_restored,
               scenario_stale_not_clobber_new,
               scenario_audit_failure_rolls_back_reset,
               scenario_audit_failure_rolls_back_restore,
               scenario_db_error_no_change,
               scenario_pool_none_dev):
        await fn()


def main():
    asyncio.run(run_all())
    print(f"ИТОГО: ✅ {_passed} пройдено, ❌ {_failed} провалено")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
