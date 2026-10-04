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

from engine.character import Character, START_ROOM
from engine.content import CLASSES
from engine.db import Database
from engine import skills
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


def _row_from_args(a, skill_offset=None):
    """Строка characters; смещение умений зависит от INSERT или save UPDATE."""
    row = {"uid": int(a[0]), "name": a[1], "cls": a[2], "race": a[3], "room": a[4],
            "level": int(a[5]), "xp": int(a[6]), "hp": int(a[7]), "mp": int(a[8]),
            "gold": int(a[9]), "equipment": a[10], "inventory": a[11],
            "quests": a[12], "flags": a[13], "generation": int(a[14]),
            "deleted_at": None}
    if skill_offset is not None and len(a) >= skill_offset + 2:
        row["learned"] = a[skill_offset]
        row["loadout"] = a[skill_offset + 1]
    return row


class FakeConn:
    def __init__(self, characters=None):
        # characters: uid -> строка-словарь (см. _row_from_args)
        self.characters = characters or {}
        self.audit = []                    # audit_log: список dict-ов
        self.identities = {}               # (platform, external_id) -> uid
        self.fail_on_execute = None        # подстрока SQL → одноразовый сбой
        self.fail_commit = False
        self._snapshot = None

    # — снимок/откат —
    def _snap(self):
        return (copy.deepcopy(self.characters), copy.deepcopy(self.audit),
                copy.deepcopy(self.identities))

    def _restore(self, snap):
        self.characters, self.audit, self.identities = (
            copy.deepcopy(snap[0]), copy.deepcopy(snap[1]), copy.deepcopy(snap[2]))

    def transaction(self):
        return _Tx(self)

    # — исполнение SQL (сопоставление по подстрокам) —
    async def execute(self, sql, *args):
        if "pg_advisory_xact_lock" in sql:
            return "SELECT 1"
        if "INSERT INTO kv_state" in sql and args[0] == 'guild_next_id':
            return "INSERT 0 1"
        if self.fail_on_execute and self.fail_on_execute in sql:
            self.fail_on_execute = None
            raise RuntimeError("injected failure")

        if "audit_log" in sql:                                    # INSERT audit
            ts, uid, action, details = args
            self.audit.append({"ts": ts, "uid": int(uid), "action": action,
                               "details": json.loads(details)})
            return "INSERT 0 1"

        if "INSERT INTO characters" in sql:                       # новый персонаж
            self.characters[int(args[0])] = _row_from_args(args, skill_offset=16)
            return "INSERT 0 1"

        if "INSERT INTO platform_identities" in sql:              # Telegram alias
            self.identities.setdefault(("telegram", str(args[0])), int(args[1]))
            return "INSERT 0 1"

        # save(): UPDATE строки СВОЕГО поколения активной записи
        if "AND generation=$15 AND deleted_at IS NULL" in sql:
            uid, gen = int(args[0]), int(args[14])
            row = self.characters.get(uid)
            if row is None or row["deleted_at"] is not None or int(row["generation"]) != gen:
                return "UPDATE 0"
            row.update(_row_from_args(args, skill_offset=15))
            row["deleted_at"] = None
            return "UPDATE 1"

        # create_character(): замена поверх удалённого (gen+1, deleted_at=NULL)
        if "generation=$15, deleted_at=NULL" in sql:
            self.characters[int(args[0])] = _row_from_args(args, skill_offset=16)
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
        if "SELECT v FROM kv_state" in sql:
            return None  # Эти lifecycle-сценарии не создают группы.
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
        if "FROM auction_listings" in sql:
            return []  # Escrow cleanup is exercised in PostgreSQL auction tests.
        if "FROM guilds" in sql or "FROM guild_members" in sql or "FROM guild_invites" in sql:
            return []  # Guild cleanup is exercised against real PostgreSQL separately.
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
    check(conn.identities.get(("telegram", "100")) == 100,
          "create: Telegram identity создана в той же транзакции")
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


async def scenario_skills_survive_restart_all_classes():
    """Обучение, выбранный порядок панели и пресет переживают загрузку у 6 классов."""
    for uid, cls in enumerate(CLASSES, 200):
        db, conn = build()
        ch = mkchar(uid=uid, cls=cls, name=f"Герой{uid}")
        ch.init_skills()
        await db.create_character(ch)
        fresh = (await db.load_all())[uid]
        check(fresh.learned == ch.learned and fresh.loadout == ch.loadout,
              f"{cls}: базовые умения записаны при создании")

        ch.level = 25
        ch.gold = 1000000
        extra = next(s for s in skills.all_class_skills(cls) if not skills.is_basic(s))
        ok, _ = skills.learn(ch, extra)
        check(ok, f"{cls}: дополнительное умение изучено")
        ch.loadout = [extra, ch.class_basics[0]]
        skills.save_preset(ch, 1)
        await db.save(ch)
        loaded = (await db.load_all())[uid]
        check(loaded.learned == ch.learned, f"{cls}: изученные умения пережили рестарт")
        check(loaded.loadout == ch.loadout, f"{cls}: выбор и порядок панели пережили рестарт")
        check(loaded.gold == ch.gold, f"{cls}: стоимость обучения сохранена вместе с умением")
        gold_before = loaded.gold
        ok, _ = skills.learn(loaded, extra)
        check(not ok and loaded.gold == gold_before, f"{cls}: повторное обучение не списывает золото")
        loaded.loadout = list(loaded.class_basics)
        check(skills.load_preset(loaded, 1) and loaded.loadout == ch.loadout,
              f"{cls}: сохранённый пресет работает после загрузки")


async def scenario_skills_restore_and_recreate():
    """Restore сохраняет умения, stale-save не затирает их, новый герой не наследует."""
    db, conn = build()
    ch = mkchar(uid=300)
    ch.init_skills()
    ch.level, ch.gold = 25, 1000000
    skills.learn(ch, "whirlwind")
    ch.loadout = ["whirlwind", "shield_wall"]
    skills.save_preset(ch, 1)
    await db.create_character(ch)
    await db.reset_character(ch.uid, ch.generation)
    restored = await db.restore_character(ch.uid)
    check(restored.learned == ch.learned and restored.loadout == ch.loadout,
          "restore: дополнительные умения и выбранная панель сохранены")
    check(restored.flags.get("presets") == ch.flags["presets"], "restore: пресеты сохранены")
    ch.learned, ch.loadout = [], []
    check(await raises(db.save(ch), StaleCharacterWrite), "stale-save: старый герой не стирает умения")
    again = (await db.load_all())[restored.uid]
    check(again.learned == restored.learned and again.loadout == restored.loadout,
          "stale-save: умения восстановленного героя остались целыми")
    await db.reset_character(restored.uid, restored.generation)
    new = mkchar(uid=restored.uid, cls="mage", name="НовыйМаг")
    new.init_skills()
    await db.create_character(new)
    loaded = (await db.load_all())[new.uid]
    check(loaded.learned == new.learned and loaded.loadout == new.loadout,
          "recreate: новый класс получает только свои умения и панель")
    check("whirlwind" not in loaded.learned and not loaded.flags.get("presets"),
          "recreate: умения и пресеты прежнего героя не наследуются")


async def scenario_remort_skill_reset_persists():
    db, conn = build()
    ch = mkchar(uid=400)
    ch.init_skills()
    ch.level, ch.gold = 25, 1000000
    skills.learn(ch, "whirlwind")
    ch.loadout = ["whirlwind"]
    skills.save_preset(ch, 1)
    ch.room = "old_dungeon"
    ch.flags["bind"] = "old_dungeon"
    ch.flags["dungeon_run"] = "old_dungeon"
    await db.create_character(ch)
    check(ch.remort(), "remort: выполнен")
    await db.save(ch)
    loaded = (await db.load_all())[ch.uid]
    check(loaded.level == 1 and loaded.learned == list(ch.class_basics),
          "remort: сброс изученных умений пережил рестарт")
    check(loaded.room == START_ROOM and loaded.flags["bind"] == START_ROOM,
          "remort: безопасная комната и точка возрождения пережили рестарт")
    check(loaded.flags["dungeon_run"] is None,
          "remort: старый данж не возобновился после рестарта")
    check(loaded.loadout == ch.loadout, "remort: базовая панель пережила рестарт")
    skills.load_preset(loaded, 1)
    check("whirlwind" not in loaded.loadout, "remort: старый пресет не возвращает сброшенное умение")


async def scenario_remort_quest_history_persists():
    db, _ = build()
    ch = mkchar(uid=401)
    ch.init_skills()
    ch.level = 25
    ch.flags["remort"] = 1
    ch.flags["quest_choices"] = {"sample_choose_faith": "light"}
    ch.quests = {"main_arrival": "done", "remort_witness": "done",
                 "remort_pack": "active", "remort_pack:kills": "1"}
    await db.create_character(ch)
    check(ch.remort(), "remort quests: новый круг выполнен")
    await db.save(ch)
    loaded = (await db.load_all())[ch.uid]
    check(loaded.remort_count == 2 and loaded.quests["main_arrival"] == "done",
          "remort quests: прежний сюжет и новый круг пережили рестарт")
    check(loaded.flags["quest_choices"]["sample_choose_faith"] == "light",
          "remort quests: прежний выбор пережил рестарт")
    check(loaded.flags["remort_quest_history"] == {"remort_witness": 1},
          "remort quests: выполненное испытание архивировано и сохранено")
    check("remort_witness" not in loaded.quests and "remort_pack:kills" not in loaded.quests,
          "remort quests: старые статусы и незавершённый счётчик очищены")


async def scenario_legacy_and_empty_loadout():
    """Старые строки получают базовые умения, но не все умения своего уровня."""
    db, conn = build()
    ch = mkchar(uid=500)
    ch.init_skills()
    await db.create_character(ch)
    legacy = dict(conn.characters[ch.uid])
    legacy.pop("learned", None)
    legacy.pop("loadout", None)
    loaded = Database._row_to_char(legacy)
    check(loaded.learned == list(ch.class_basics) and loaded.loadout == list(ch.class_basics),
          "legacy: строка без новых колонок получает базовые умения")
    legacy.update(learned="[]", loadout="[]")
    loaded = Database._row_to_char(legacy)
    check(loaded.learned == list(ch.class_basics), "legacy: пустые колонки миграции дают только базовые умения")
    check(loaded.gold == ch.gold, "legacy: загрузка не списывает и не выдаёт золото")
    ch.loadout = []
    await db.save(ch)
    loaded = (await db.load_all())[ch.uid]
    check(loaded.learned == ch.learned and loaded.loadout == [],
          "save/load: явно пустая панель не заменяется другим набором")


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
               scenario_pool_none_dev,
               scenario_skills_survive_restart_all_classes,
               scenario_skills_restore_and_recreate,
               scenario_remort_skill_reset_persists,
               scenario_remort_quest_history_persists,
               scenario_legacy_and_empty_loadout):
        await fn()


def main():
    asyncio.run(run_all())
    print(f"ИТОГО: ✅ {_passed} пройдено, ❌ {_failed} провалено")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
