# -*- coding: utf-8 -*-
"""
Автономные тесты модерации и TZ-уведомлений (Этап 7.2) — без Telegram и БД.
Запуск:
    python test_moderation.py

Покрывает:
  • engine/moderation.py: ban/unban/mute/unmute, экспирацию мута по времени,
    запись audit_log (через мок-БД), чат-rate-limit (окно 5/10с);
  • engine/textsafe.py: блок-список имён (имперсонация/мат) и пропуск нормальных;
  • engine/notify.py: тихие часы в ЛОКАЛЬНОМ времени игрока (tz_offset, границы
    22:59/23:00/08:59/09:00 при разных смещениях) и учёт квоты broadcast_all
    (record_sent + allow — чистая часть рассылки).
"""
import asyncio
import calendar
import sys

from engine import moderation as mod
from engine import notify
from engine import textsafe as ts
from engine.character import Character

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


def new_char(uid=1):
    return Character(uid=uid, name="Тест", cls="warrior", race="human")


def utc_ts(h, mi=0, d=2):
    """unix-время для заданного ЧАСА UTC (детерминированно, без локали машины)."""
    return float(calendar.timegm((2026, 7, d, h, mi, 0, 0, 0, 0)))


# ─────────────────────── мок-БД для модерации ───────────────────────
class MockDB:
    """Минимальная заглушка: копит аудит и состояние модерации в памяти."""

    def __init__(self, preset=None):
        self.audits = []                 # [(uid, action, details), ...]
        self.mod = dict(preset or {})    # uid -> row-dict
        self.pool = object()             # не None: методы не деградируют «в тишину»

    async def add_audit(self, uid, action, details=None):
        self.audits.append((uid, action, details or {}))

    async def set_moderation(self, uid, banned, muted_until, reason="", by_admin=0):
        self.mod[uid] = {"uid": uid, "banned": banned, "muted_until": muted_until,
                         "reason": reason, "by_admin": by_admin, "updated": 0.0}

    async def load_moderation(self):
        return list(self.mod.values())


# ═══════════════════════ 1. БАН / АНБАН ═══════════════════════
print("\n[1] Бан / анбан + аудит")


async def _t_ban():
    mod.reset()
    db = MockDB()
    mod.set_db(db)
    check("по умолчанию не забанен", mod.is_banned(42) is False)
    await mod.ban(42, reason="токсичность", by=777)
    check("после ban — забанен", mod.is_banned(42) is True)
    check("ban пишет audit mod_ban", db.audits and db.audits[-1][1] == "mod_ban")
    check("audit хранит reason", db.audits[-1][2].get("reason") == "токсичность")
    check("audit хранит by (админ)", db.audits[-1][2].get("by") == 777)
    check("ban персистит в БД (set_moderation)", db.mod.get(42, {}).get("banned") is True)
    await mod.unban(42, by=777)
    check("после unban — не забанен", mod.is_banned(42) is False)
    check("unban пишет audit mod_unban", db.audits[-1][1] == "mod_unban")
    check("unban персистит banned=False", db.mod.get(42, {}).get("banned") is False)


asyncio.run(_t_ban())

# ═══════════════════════ 2. МУТ / ЭКСПИРАЦИЯ ═══════════════════════
print("\n[2] Мут / анмут / экспирация по времени")


async def _t_mute():
    mod.reset()
    db = MockDB()
    mod.set_db(db)
    now = 1_000_000.0
    check("по умолчанию не в муте", mod.is_muted(7, now) is False)
    await mod.mute(7, minutes=60, reason="флуд", by=1, now=now)
    check("сразу после mute — в муте", mod.is_muted(7, now) is True)
    check("в муте спустя 30 мин", mod.is_muted(7, now + 30 * 60) is True)
    check("в муте на границе −1с до конца", mod.is_muted(7, now + 3600 - 1) is True)
    check("НЕ в муте по истечении часа", mod.is_muted(7, now + 3601) is False)
    check("mute пишет audit mod_mute", any(a[1] == "mod_mute" for a in db.audits))
    check("audit мута хранит minutes", db.audits[-1][2].get("minutes") == 60)
    check("muted_until в будущем", mod.muted_until(7) > now)
    await mod.unmute(7, by=1)
    check("после unmute — не в муте", mod.is_muted(7, now) is False)
    check("unmute пишет audit mod_unmute", db.audits[-1][1] == "mod_unmute")


asyncio.run(_t_mute())

# ═══════════════════════ 3. ЗАГРУЗКА КЭША ИЗ БД ═══════════════════════
print("\n[3] load() поднимает состояние из БД")


async def _t_load():
    mod.reset()
    preset = {5: {"uid": 5, "banned": True, "muted_until": 0.0,
                  "reason": "r", "by_admin": 9, "updated": 0.0},
              6: {"uid": 6, "banned": False, "muted_until": 9_999_999_999.0,
                  "reason": "", "by_admin": 9, "updated": 0.0}}
    db = MockDB(preset)
    mod.set_db(db)
    await mod.load()
    check("бан поднят из БД", mod.is_banned(5) is True)
    check("мут поднят из БД", mod.is_muted(6, 1_000_000.0) is True)
    check("незатронутый uid чист", mod.is_banned(6) is False)


asyncio.run(_t_load())

# ═══════════════════════ 4. ЧАТ-RATE-LIMIT ═══════════════════════
print("\n[4] chat_allowed: окно 5 сообщений / 10 секунд")
mod.reset()
_u = 100
_t0 = 5000.0
_res = [mod.chat_allowed(_u, _t0 + i * 0.1) for i in range(6)]
check("первые 5 сообщений разрешены", all(_res[:5]))
check("6-е в окне — отклонено", _res[5] is False)
check("после сдвига окна (>10с) снова разрешено",
      mod.chat_allowed(_u, _t0 + 11.0) is True)
mod.reset()
# ровно на границе окна старые метки выпадают
for i in range(5):
    mod.chat_allowed(200, 0.0 + i * 0.01)
check("на 10с ровно старые метки ещё считаются (отказ)",
      mod.chat_allowed(200, 10.0 - 0.001) is False)
check("на 10.01с окно очистилось (разрешено)",
      mod.chat_allowed(200, 10.02) is True)

# ═══════════════════════ 5. ФИЛЬТР ИМЁН ═══════════════════════
print("\n[5] clean_name: блок-список и нормальные имена")
check("блок: admin", ts.clean_name("SuperAdmin") is None)
check("блок: админ (рус)", ts.clean_name("Админ") is None)
check("блок: moderator", ts.clean_name("Moderator") is None)
check("блок: система", ts.clean_name("Системный") is None)
check("блок: поддержка", ts.clean_name("Поддержка") is None)
check("блок: мат-корень", ts.clean_name("Мудак") is None)
check("норм: Гэндальф проходит", ts.clean_name("Гэндальф") == "Гэндальф")
check("норм: Себастьян проходит (не ложное срабатывание)",
      ts.clean_name("Себастьян") == "Себастьян")
check("норм: Aragorn проходит", ts.clean_name("Aragorn") == "Aragorn")
check("name_reason для admin непуст", ts.name_reason("Admin") != "")
check("name_reason для нормального пуст", ts.name_reason("Гэндальф") == "")
check("name_reason для короткого — про длину",
      "2" in ts.name_reason("я"))

# ═══════════════════════ 6. TZ: ТИХИЕ ЧАСЫ ЛОКАЛЬНО ═══════════════════════
print("\n[6] is_quiet в локальном времени игрока (tz_offset)")


def ch_tz(offset):
    c = new_char()
    notify.set_tz_offset(c, offset)
    return c


# offset 0: локальное время == UTC
c0 = ch_tz(0)
check("tz0: 23:00 UTC — тихо", notify.is_quiet(utc_ts(23), c0) is True)
check("tz0: 22:59 UTC — НЕ тихо", notify.is_quiet(utc_ts(22, 59), c0) is False)
check("tz0: 08:59 UTC — тихо", notify.is_quiet(utc_ts(8, 59), c0) is True)
check("tz0: 09:00 UTC — НЕ тихо", notify.is_quiet(utc_ts(9), c0) is False)

# offset +3 (МСК): локальные 23:00 == 20:00 UTC
c3 = ch_tz(3)
check("tz+3: 20:00 UTC (23 локал) — тихо", notify.is_quiet(utc_ts(20), c3) is True)
check("tz+3: 19:59 UTC (22:59 локал) — НЕ тихо", notify.is_quiet(utc_ts(19, 59), c3) is False)
check("tz+3: 05:59 UTC (08:59 локал) — тихо", notify.is_quiet(utc_ts(5, 59), c3) is True)
check("tz+3: 06:00 UTC (09:00 локал) — НЕ тихо", notify.is_quiet(utc_ts(6), c3) is False)

# offset -2: локальные 23:00 == 01:00 UTC
cm2 = ch_tz(-2)
check("tz−2: 01:00 UTC (23 локал) — тихо", notify.is_quiet(utc_ts(1), cm2) is True)
check("tz−2: 00:59 UTC (22:59 локал) — НЕ тихо", notify.is_quiet(utc_ts(0, 59), cm2) is False)
check("tz−2: 10:59 UTC (08:59 локал) — тихо", notify.is_quiet(utc_ts(10, 59), cm2) is True)
check("tz−2: 11:00 UTC (09:00 локал) — НЕ тихо", notify.is_quiet(utc_ts(11), cm2) is False)

# дефолт (+3) при незаданном tz + кламп диапазона
check("дефолт tz = +3 (МСК)", notify.tz_offset(new_char()) == 3)
check("кламп: +99 → дефолт", notify.tz_offset(ch_tz(99)) == notify.DEFAULT_TZ or
      notify.tz_offset(ch_tz(99)) == 12)
_cc = new_char()
notify.set_tz_offset(_cc, 50)   # вне диапазона → кламп к +12
check("set_tz_offset клампит к TZ_MAX", _cc.flags["notify"]["tz_offset"] == 12)
check("quiet_off отменяет тихие часы даже ночью",
      (lambda c: (notify.set_quiet_off(c, True),
                  notify.is_quiet(utc_ts(2), c))[1])(ch_tz(0)) is False)

# ═══════════════════════ 7. КВОТА BROADCAST (чистая часть) ═══════════════════════
print("\n[7] record_sent: квота broadcast_all соблюдается")
_bc = new_char()
_day = utc_ts(12)          # локально 15:00 (+3) — не тихие часы
_lim = notify.limit(_bc)   # дефолт 2
check("на старте квота = лимит", notify.quota_left(_bc, _day) == _lim)
check("world_event разрешён при свободной квоте",
      notify.allow(_bc, "world_event", _day) == "send")
for _ in range(_lim):
    notify.record_sent(_bc, "world_event", _day)
check("после лимита record_sent квота исчерпана",
      notify.quota_left(_bc, _day) == 0)
check("world_event теперь drop (лимит соблюдён)",
      notify.allow(_bc, "world_event", _day) == "drop")
check("world_boss тоже drop при исчерпанной квоте",
      notify.allow(_bc, "world_boss", _day) == "drop")
# off-quota (сделки) не расходуют лимит
_bc2 = new_char()
for _ in range(5):
    notify.record_sent(_bc2, "auction_sold", _day)
check("auction_sold (off-quota) не трогает счётчик",
      notify.quota_left(_bc2, _day) == notify.limit(_bc2))
check("auction_sold всегда send",
      notify.allow(_bc2, "auction_sold", _day) == "send")

# ─────────────────────── ИТОГ ───────────────────────
print("\n" + "═" * 50)
print(f"ИТОГО: ✅ {_passed} пройдено, ❌ {_failed} провалено")
print("═" * 50)
sys.exit(1 if _failed else 0)
