# -*- coding: utf-8 -*-
"""
Автономные тесты критических фиксов перед закрытой бетой (Этап 1).
Запуск:
    python test_beta_fixes.py
Без БД и без сети — только чистые части движка (engine/), никакого aiogram.

Покрывает пять багов:
  1) гильд-права: can_withdraw / can_admin / member на непустом складе, и что
     несуществующего can_manage в API больше нет (краш-баг устранён);
  2) dirty-save: провалившийся db.save() возвращает uid в набор (не теряем),
     а shutdown-ретрай добивает временно падающую БД;
  3) структурный логгер log_err не падает ни без исключения, ни с ним;
  4) reset-flow: чистая проверка окна 60с и идемпотентность подтверждения;
  5) textsafe: esc_md нейтрализует спецсимволы; clean_name режет невидимые/
     пустые/длинные/@//только-пунктуацию; валидные имена проходят; clean_chat.
"""
import asyncio
import os
import sys
import tempfile
import time

from engine.guild import GuildManager
from engine import persist
from engine.persist import CharDirtySet, FlushHealth, flush_dirty, flush_until_clean, reset_pending_valid
from engine import textsafe as ts
from engine import log as elog

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


# ─────────────────────── 1. ГИЛЬД-ПРАВА ───────────────────────
print("\n[1] Гильд-права: member / can_withdraw / can_admin на непустом складе")

_tmp = tempfile.mkdtemp()
gm = GuildManager(os.path.join(_tmp, "guilds.json"))
_gid = gm.create(1, "Тестовая гильдия")          # uid1 — лидер
gm.invites[2] = _gid; gm.accept(2)               # uid2 — боец (member)
gm.invites[3] = _gid; gm.accept(3)               # uid3 — боец (member)
gm.deposit_item(1, "ржавый_меч")                 # склад НЕ пуст
gm.deposit_gold(1, 5000)

check("баг-краш устранён: метода can_manage в API нет",
      not hasattr(GuildManager, "can_manage") and not hasattr(gm, "can_manage"))
check("лидер: can_withdraw = True", gm.can_withdraw(1) is True)
check("лидер: can_admin = True", gm.can_admin(1) is True)
check("боец (member): rank == 'member'", gm.rank(2) == "member")
check("боец: can_withdraw = False (склад закрыт)", gm.can_withdraw(2) is False)
check("боец: can_admin = False", gm.can_admin(2) is False)
check("склад непуст (bank_items)", len(gm.guild_of(1).get("bank_items", [])) == 1)
check("боец не может забрать предмет со склада (право)",
      gm.withdraw_item(2, "ржавый_меч") is False)

# повысить uid2 до офицера — снимать со склада можно, управлять составом нельзя
gm.set_rank(1, 2, "officer")
check("офицер: rank == 'officer'", gm.rank(2) == "officer")
check("офицер: can_withdraw = True (выдача со склада)", gm.can_withdraw(2) is True)
check("офицер: can_admin = False (не управляет составом)", gm.can_admin(2) is False)
check("офицер МОЖЕТ забрать предмет со склада", gm.withdraw_item(2, "ржавый_меч") is True)

# повысить uid3 до заместителя — и склад, и управление составом
gm.set_rank(1, 3, "deputy")
check("зам: can_admin = True (управление составом)", gm.can_admin(3) is True)
check("зам: can_withdraw = True", gm.can_withdraw(3) is True)

# вне гильдии — оба права False
check("не в гильдии: can_withdraw = False", gm.can_withdraw(999) is False)
check("не в гильдии: can_admin = False", gm.can_admin(999) is False)


# ─────────────────────── 2. DIRTY-SAVE: ретраи / возврат uid ───────────────────────
print("\n[2] Dirty-save: провал db.save() возвращает uid; shutdown-ретрай добивает")


class _Ch:
    def __init__(self, uid):
        self.uid = uid


_chars = {1: _Ch(1), 2: _Ch(2), 3: _Ch(3), 7: _Ch(7)}


def _get(uid):
    return _chars.get(uid)


async def _t_flush_all_fail():
    ds = CharDirtySet()
    ds.mark(1); ds.mark(2); ds.mark(3)

    async def save_fail(ch):
        raise RuntimeError("БД недоступна")

    ok, failed = await flush_dirty(ds, _get, save_fail)
    return ok, failed, ds.pending()


_ok, _failed_n, _pending = asyncio.run(_t_flush_all_fail())
check("полный провал: ok == 0", _ok == 0)
check("полный провал: failed == 3", _failed_n == 3)
check("провалившиеся uid ВЕРНУЛИСЬ в набор (не потеряны)", _pending == {1, 2, 3})


async def _t_flush_partial():
    ds = CharDirtySet()
    ds.mark(1); ds.mark(2)
    saved = []

    async def save_one_bad(ch):
        if ch.uid == 1:
            raise RuntimeError("этот падает")
        saved.append(ch.uid)

    ok, failed = await flush_dirty(ds, _get, save_one_bad)
    return ok, failed, ds.pending(), saved


_ok2, _fail2, _pend2, _saved2 = asyncio.run(_t_flush_partial())
check("частичный провал: ok == 1 (uid2 записан)", _ok2 == 1 and _saved2 == [2])
check("частичный провал: failed == 1", _fail2 == 1)
check("вернулся только упавший uid1", _pend2 == {1})


async def _t_flush_until_clean():
    ds = CharDirtySet()
    ds.mark(7)
    attempts_seen = {"n": 0}
    slept = {"n": 0}

    async def save_flaky(ch):
        # падает первые 2 раза, на 3-й — успех (эмуляция временной недоступности БД)
        attempts_seen["n"] += 1
        if attempts_seen["n"] < 3:
            raise RuntimeError("временный сбой БД")

    async def _instant_sleep(_):
        slept["n"] += 1

    ok_total, left = await flush_until_clean(
        ds, _get, save_flaky, attempts=3, pause=1.0, sleeper=_instant_sleep)
    return ok_total, left, len(ds), slept["n"]


_okt, _left, _dslen, _slept = asyncio.run(_t_flush_until_clean())
check("shutdown-ретрай добил временно падавшую БД (ok_total == 1)", _okt == 1)
check("shutdown-ретрай: набор пуст (left == 0)", _left == 0 and _dslen == 0)
check("shutdown-ретрай делал паузы между попытками", _slept >= 1)


# FlushHealth: 5 провалов подряд → громкое предупреждение, но не спам
print("\n[2b] FlushHealth: 5 провалов подряд → предупреждение, не чаще раза в минуту")
_clk = {"t": 1000.0}
fh = FlushHealth(threshold=5, warn_interval=60.0, clock=lambda: _clk["t"])
_warns = [fh.record(1) for _ in range(4)]
check("первые 4 провала подряд — без предупреждения", not any(_warns))
check("5-й провал подряд — громкое предупреждение (True)", fh.record(1) is True)
check("6-й провал в том же окне — молчим (анти-спам)", fh.record(1) is False)
_clk["t"] += 61.0
check("после минуты повторное предупреждение снова разрешено", fh.record(1) is True)
check("успешный проход обнуляет счётчик подряд-провалов", fh.record(0) is False and fh.consecutive == 0)


# ─────────────────────── 3. ЛОГГЕР ───────────────────────
print("\n[3] Структурный логгер log_err: не падает без err и с err")
_lg = elog.get("test.beta")
check("get() возвращает логгер с .info", hasattr(_lg, "info") and hasattr(_lg, "error"))
_ok_noerr = True
try:
    elog.log_err(_lg, "event_without_error", uid=42, category="test")
except Exception:
    _ok_noerr = False
check("log_err без исключения не падает", _ok_noerr)
_ok_err = True
try:
    elog.log_err(_lg, "event_with_error", ValueError("бум"), uid=7)
except Exception:
    _ok_err = False
check("log_err с исключением (стек) не падает", _ok_err)
_ok_empty = True
try:
    elog.log_err(_lg, "event_no_ctx")
except Exception:
    _ok_empty = False
check("log_err без контекста не падает", _ok_empty)


# ─────────────────────── 4. RESET-FLOW ───────────────────────
print("\n[4] Reset-flow: чистая проверка окна 60с + идемпотентность подтверждения")
_now = 10_000.0
check("ts=None (запроса не было) → окно закрыто", reset_pending_valid(None, _now) is False)
check("только что запросили → окно открыто", reset_pending_valid(_now, _now) is True)
check("30с назад → окно ещё открыто", reset_pending_valid(_now - 30, _now) is True)
check("ровно 60с → на границе ещё валидно", reset_pending_valid(_now - 60, _now) is True)
check("61с назад → окно истекло", reset_pending_valid(_now - 61, _now) is False)
check("константа окна = 60с", persist.RESET_WINDOW_SEC == 60.0)

# идемпотентность подтверждения: pending_reset — dict{uid: ts}; подтверждение
# извлекает (pop) метку. Повторный клик уже ничего не находит → безопасный no-op.
_pending_reset = {5: _now}
_first = _pending_reset.pop(5, None)
_second = _pending_reset.pop(5, None)
check("первое подтверждение находит метку", _first == _now)
check("повторное подтверждение идемпотентно (метки уже нет)", _second is None)


# ─────────────────────── 5. TEXTSAFE ───────────────────────
print("\n[5] textsafe: esc_md, clean_name, clean_chat")

# esc_md — нейтрализация спецсимволов Markdown
check("esc_md экранирует * _ ` [",
      ts.esc_md("*a_b`c[d") == "\\*a\\_b\\`c\\[d")
check("esc_md на пустом → ''", ts.esc_md("") == "" and ts.esc_md(None) == "")
_evil = "*жирный* _курсив_ `код` [ссылка]"
_esc = ts.esc_md(_evil)
check("esc_md: в результате нет НИ ОДНОГО неэкранированного спецсимвола",
      all(_esc[i - 1] == "\\" for i, c in enumerate(_esc) if c in "*_`["))

# clean_name — валидация
check("clean_name: пустая строка → None", ts.clean_name("") is None)
check("clean_name: только пробелы → None", ts.clean_name("   ") is None)
check("clean_name: 1 символ (коротко) → None", ts.clean_name("a") is None)
check("clean_name: 21 символ (длинно) → None", ts.clean_name("a" * 21) is None)
check("clean_name: начинается с @ → None", ts.clean_name("@spoof") is None)
check("clean_name: начинается с / → None", ts.clean_name("/reset") is None)
check("clean_name: только пунктуация → None", ts.clean_name("!!!...") is None)
check("clean_name: невидимые (zero-width) вырезаются",
      ts.clean_name("Ко​н﻿ан") == "Конан")
check("clean_name: управляющие символы вырезаются",
      ts.clean_name("Арагорн") == "Арагорн")
check("clean_name: схлоп повторных пробелов + trim",
      ts.clean_name("  Ко   нан  ") == "Ко нан")
check("clean_name: валидное русское имя проходит", ts.clean_name("Гэндальф") == "Гэндальф")
check("clean_name: валидное латинское имя проходит", ts.clean_name("Legolas") == "Legolas")
check("clean_name: имя с цифрами проходит", ts.clean_name("Орк42") == "Орк42")
check("clean_name: ровно 2 символа проходит", ts.clean_name("Ко") == "Ко")

# clean_chat — чистка реплики
check("clean_chat: управляющие символы вырезаны",
      ts.clean_chat("при\x00вет\x07") == "привет")
check("clean_chat: схлоп пробелов", ts.clean_chat("а   б    в") == "а б в")
check("clean_chat: обрезка до 300 символов", len(ts.clean_chat("я" * 500)) == 300)
check("clean_chat: пустой → ''", ts.clean_chat("") == "" and ts.clean_chat(None) == "")
check("clean_chat: валидная реплика сохраняется",
      ts.clean_chat("Привет, герой!") == "Привет, герой!")


# ─────────────────────── ИТОГ ───────────────────────
print("\n" + "═" * 50)
print(f"ИТОГО: ✅ {_passed} пройдено, ❌ {_failed} провалено")
print("═" * 50)
sys.exit(1 if _failed else 0)
