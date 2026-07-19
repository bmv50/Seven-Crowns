# -*- coding: utf-8 -*-
"""
Автономные тесты персистентности рантайма (без Telegram и без PostgreSQL).
Запуск из каталога проекта:
    python test_persistence.py

Проверяет:
  • World.snapshot()/restore(): применение hp/dead_at к живым инстансам;
    восстановление трупов с decay; несовпадающий контент пропускается без ошибок;
  • JSON-safety снимков (json.dumps проходит);
  • AuctionManager.export_state()/import_state() — круговая сериализация;
  • файловый fallback аукциона при pool=None не сломан;
  • territory export_state()/import_state() круговая сериализация;
  • kv_state-слой БД при pool=None: kv_set — no-op, kv_get — None;
  • дебаунс: CharDirtySet накапливает грязных, force-путь (discard) пишет мимо набора.
"""
import sys
import json
import time
import asyncio
import tempfile
import os

random_seed_note = None

from engine.world import World, MobInstance
from engine.persist import CharDirtySet
from engine.auction import AuctionManager
from engine import territory as T
from engine.db import Database, SCHEMA

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✅ {name}")
    else:
        _failed += 1
        print(f"  ❌ {name}")


def _first_room_with_spawns(w):
    for rid, lst in w.mobs.items():
        if len(lst) >= 1:
            return rid
    return None


# ─────────────────────── 1. СНАПШОТ/ВОССТАНОВЛЕНИЕ МОБОВ ───────────────────────
print("\n[1] World.snapshot()/restore(): HP и dead_at")

w = World()
_room = _first_room_with_spawns(w)
check("в мире есть комната со спавнами", _room is not None)
_inst0 = w.mobs[_room][0]
_mob_id0 = _inst0.mob_id
_maxhp0 = _inst0.max_hp

# изменим состояние: ранить одного, убить (если в комнате есть второй) — иначе просто ранить
_inst0.hp = _maxhp0 // 3
_killed_room = None
_killed_idx = None
for rid, lst in w.mobs.items():
    if len(lst) >= 1:
        # найдём инстанс, который сделаем мёртвым (не тот же самый, что ранили, если можно)
        for idx, inst in enumerate(lst):
            if not (rid == _room and idx == 0):
                inst.dead_at = time.time() - 5.0
                _killed_room, _killed_idx = rid, idx
                break
    if _killed_room:
        break

snap = w.snapshot()
check("snapshot() -> dict с ключами mobs/corpses", "mobs" in snap and "corpses" in snap)
check("snapshot JSON-safe (json.dumps проходит)", isinstance(json.dumps(snap), str))

# новый мир (как после рестарта) — состояние свежее
w2 = World()
check("свежий мир: раненого моба нет (HP полный)", w2.mobs[_room][0].hp == _maxhp0)
_applied = w2.restore(snap)
check("restore() вернул число применённых записей > 0", _applied > 0)
check("restore применил HP раненого моба", w2.mobs[_room][0].hp == _maxhp0 // 3)
check("restore применил dead_at убитого моба",
      _killed_room is not None and w2.mobs[_killed_room][_killed_idx].dead_at is not None)
check("живой моб остался живым после restore", w2.mobs[_room][0].alive)

# HP не выходит за максимум даже если в снимке завышен
_bad = {"mobs": [{"room": _room, "idx": 0, "mob_id": _mob_id0, "hp": 10**9, "dead_at": None}],
        "corpses": []}
w3 = World()
w3.restore(_bad)
check("restore ограничивает HP максимумом инстанса", w3.mobs[_room][0].hp == w3.mobs[_room][0].max_hp)

# «живой труп» защита: dead_at None но hp<=0 -> лечим до макс
_ghost = {"mobs": [{"room": _room, "idx": 0, "mob_id": _mob_id0, "hp": 0, "dead_at": None}],
          "corpses": []}
w3b = World()
w3b.restore(_ghost)
check("живой моб с hp<=0 в снимке -> восстановлен до max (без 'живого трупа')",
      w3b.mobs[_room][0].alive and w3b.mobs[_room][0].hp == w3b.mobs[_room][0].max_hp)


# ─────────────────────── 2. НЕСОВПАДЕНИЕ КОНТЕНТА ───────────────────────
print("\n[2] restore(): несовпадающий контент пропускается без ошибок")

w4 = World()
_before_hp = w4.mobs[_room][0].hp
# запись с чужим mob_id на индексе 0 — должна отсеяться
_mismatch = {"mobs": [
    {"room": _room, "idx": 0, "mob_id": "___несуществующий_моб___", "hp": 1, "dead_at": None},
    {"room": "___нет_такой_комнаты___", "idx": 0, "mob_id": _mob_id0, "hp": 1, "dead_at": None},
    {"room": _room, "idx": 99999, "mob_id": _mob_id0, "hp": 1, "dead_at": None},
], "corpses": []}
_n = 0
try:
    _n = w4.restore(_mismatch)
    _ok_nothrow = True
except Exception as e:
    _ok_nothrow = False
    print("   исключение:", e)
check("restore не падает на несовпадениях контента", _ok_nothrow)
check("несовпадающие записи не применились (0 применено)", _n == 0)
check("HP моба на индексе 0 не тронут чужой записью", w4.mobs[_room][0].hp == _before_hp)

# пустой/None вход
check("restore(None) -> 0, без ошибок", World().restore(None) == 0)
check("restore({}) -> 0, без ошибок", World().restore({}) == 0)


# ─────────────────────── 3. ТРУПЫ С DECAY ───────────────────────
print("\n[3] Снапшот/восстановление трупов с decay")

w5 = World()
# создадим два трупа: один свежий, один почти истлевший
_freshmob = w5.mobs[_room][0]
_c_fresh = w5.add_corpse(_room, _freshmob, ["малое_зелье"])
_c_old = w5.add_corpse(_room, _freshmob, ["малое_зелье"])
# состарим второй так, чтобы при decay(ttl=180) он выпал
_c_old["dead_at"] = time.time() - 10000.0

snap5 = w5.snapshot()
check("снимок трупов JSON-safe", isinstance(json.dumps(snap5), str))
check("в снимке два трупа", len(snap5["corpses"]) == 2)

w6 = World()
check("свежий мир без трупов", len(w6.corpses_in(_room)) == 0)
w6.restore(snap5)
check("после restore трупы на месте (2)", len(w6.corpses_in(_room)) == 2)
# лут восстановлен
_loot_keys = [c["loot"] for c in w6.corpses_in(_room)]
check("лут трупов восстановлен", all("малое_зелье" in l for l in _loot_keys))
# decay убирает истлевший
w6.process_corpse_decay(ttl=180.0)
check("process_corpse_decay убрал истлевший труп (остался 1)",
      len(w6.corpses_in(_room)) == 1)
check("свежий труп пережил decay", w6.corpses_in(_room)[0]["dead_at"] > time.time() - 180)

# corpse_seq продолжается (ключи не пересекаются)
w7 = World()
w7.restore(snap5)
_newc = w7.add_corpse(_room, _freshmob, [])
_existing_keys = {c["key"] for c in w7.corpses_in(_room)}
check("новый труп после restore получает уникальный ключ",
      _existing_keys == set(dict.fromkeys(_existing_keys)) and len(_existing_keys) == 3)

# труп в исчезнувшей комнате пропускается
w8 = World()
_bad_corpse = {"mobs": [], "corpses": [
    {"room": "___нет_такой_комнаты___", "key": "corpse:1", "mob_id": _mob_id0,
     "name": "x", "emoji": "💀", "loot": [], "dead_at": time.time()}]}
check("restore трупа в несуществующей комнате не падает и не применяется",
      w8.restore(_bad_corpse) == 0)


# ─────────────────────── 4. АУКЦИОН: export/import круговая ───────────────────────
print("\n[4] Auction export_state()/import_state() круговая сериализация")

_tmpdir = tempfile.mkdtemp()
_auc_path = os.path.join(_tmpdir, "auction.json")
am = AuctionManager(_auc_path)
am.db_mode = True    # эмулируем БД-режим (save метит dirty, не пишет файл)
_lid = am.create_listing(42, "Продавец", "ржавый_меч", 500)
check("создан лот", _lid is not None)
check("db_mode: create помечает dirty", am.dirty is True)
# покупка -> выручка в payouts
am.dirty = False
_status, _lot = am.buy(_lid, 99)
check("лот куплен", _status == "ok")
check("выручка попала продавцу (payouts>0)", am.pending_payout(42) > 0)

_exp = am.export_state()
check("export_state JSON-safe (json.dumps проходит)", isinstance(json.dumps(_exp), str))

am2 = AuctionManager(os.path.join(_tmpdir, "auction2.json"))
am2.import_state(json.loads(json.dumps(_exp)))
check("import_state восстановил payouts", am2.pending_payout(42) == am.pending_payout(42))
check("import_state восстановил счётчик next", am2._next == am._next)
check("import_state сбросил dirty", am2.dirty is False)


# ─────────────────────── 5. АУКЦИОН: файловый fallback (pool=None) ───────────────────────
print("\n[5] Auction файловый fallback при pool=None не сломан")

_fpath = os.path.join(_tmpdir, "auction_file.json")
amf = AuctionManager(_fpath)      # db_mode=False по умолчанию — как без БД
_lidf = amf.create_listing(7, "Хранитель", "малое_зелье", 100)
check("без db_mode create_listing работает", _lidf is not None)
check("файл аукциона записан на диск (save() -> файл)", os.path.exists(_fpath))
# перечитать с диска новым менеджером
amf2 = AuctionManager(_fpath)
check("состояние читается обратно из файла (лот на месте)", amf2.get(_lidf) is not None)
check("db_mode остаётся False по умолчанию (обратная совместимость)", amf2.db_mode is False)
check("dirty не выставляется в файловом режиме", amf2.dirty is False)


# ─────────────────────── 6. ТЕРРИТОРИИ: export/import круговая ───────────────────────
print("\n[6] Territory export_state()/import_state() круговая сериализация")

T.import_state({})   # чистый старт
_zone = sorted(T.CONTESTED)[0]


class _FakeCh:
    def __init__(self):
        self.flags = {"rep": {}}


_ch = _FakeCh()
_fac = T.ZONE_FACTION[_zone]
_ch.flags["rep"] = {_fac: 100}   # игрок предан коренной фракции зоны
for _ in range(3):
    T.add_kill(_ch, _zone)
_exp_t = T.export_state()
check("territory export_state JSON-safe", isinstance(json.dumps(_exp_t), str))
check("контроль зоны накопился", _exp_t.get(_zone, {}).get(_fac, 0) == 3)

# круговая: сбросить, восстановить
T.import_state({})
check("после import({}) контроль пуст", T.export_state() == {})
T.import_state(json.loads(json.dumps(_exp_t)))
check("после import восстановлен доминант зоны", T.dominant(_zone) == _fac)

# db_mode: save(path) метит dirty вместо файла
T.set_db_mode(True)
T.mark_clean()
_terr_file = os.path.join(_tmpdir, "territory.json")
T.save(_terr_file)
check("db_mode: territory.save() не пишет файл", not os.path.exists(_terr_file))
check("db_mode: territory.save() метит dirty", T.is_dirty() is True)
# без db_mode пишет файл
T.set_db_mode(False)
T.save(_terr_file)
check("без db_mode: territory.save() пишет файл", os.path.exists(_terr_file))
T.set_db_mode(False)
T.import_state({})   # прибрать за собой


# ─────────────────────── 7. KV_STATE слой БД (pool=None) ───────────────────────
print("\n[7] db.kv_set/kv_get при pool=None (no-op / None)")

check("SCHEMA содержит таблицу kv_state", "kv_state" in SCHEMA)


async def _kv_none_test():
    db = Database()
    db.pool = None
    await db.kv_set("world", {"a": 1})       # no-op, не должно падать
    v = await db.kv_get("world")
    return v


_v = asyncio.run(_kv_none_test())
check("pool=None: kv_get возвращает None", _v is None)
check("pool=None: kv_set не бросает исключение (no-op)", True)


# ─────────────────────── 8. ДЕБАУНС: CharDirtySet ───────────────────────
print("\n[8] Дебаунс: CharDirtySet накапливает; force-путь пишет мимо набора")

ds = CharDirtySet()
check("новый набор пуст", len(ds) == 0)
ds.mark(1)
ds.mark(2)
ds.mark(1)             # повтор не дублируется (set)
check("mark накапливает уникальные uid", len(ds) == 2)
check("__contains__ работает", 1 in ds and 2 in ds and 3 not in ds)
check("has() работает", ds.has(1) and not ds.has(3))
check("pending() отдаёт копию (не опустошает)", ds.pending() == {1, 2} and len(ds) == 2)

# force-путь: bot.save(force=True) вызывает discard(uid) + немедленную запись,
# т.е. uid не остаётся висеть в наборе как «ожидающий отложенной записи»
ds.mark(5)
ds.discard(5)
check("discard убирает uid из набора (эмуляция force)", 5 not in ds)

# drain опустошает и возвращает всё
_drained = ds.drain()
check("drain вернул накопленных грязных", _drained == {1, 2})
check("после drain набор пуст", len(ds) == 0)
check("повторный drain -> пустое множество", ds.drain() == set())

# сценарий дебаунса целиком: пометили 3 действия одного игрока -> одна запись
ds2 = CharDirtySet()
for _ in range(3):
    ds2.mark(777)      # 3 «действия» подряд
check("3 действия одного игрока -> 1 запись в drain (дебаунс)", ds2.drain() == {777})


# ─────────────────────── ИТОГ ───────────────────────
print("\n" + "═" * 50)
print(f"ИТОГО: ✅ {_passed} пройдено, ❌ {_failed} провалено")
print("═" * 50)
sys.exit(1 if _failed else 0)
