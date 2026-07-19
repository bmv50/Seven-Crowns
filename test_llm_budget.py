# -*- coding: utf-8 -*-
"""
Этап 8 — тесты ИИ-слоя: журнал вызовов (ai/llmlog), дневной HARD-бюджет и
аварийное отключение (ai/cost.BudgetGuard), персист лимитов (TokenBucket),
обеззараживание текста модели (ai/textguard), версии промптов и «фишка первых
10 минут» (chronicle newcomer).

Без Postgres, без сети: llmlog копит в память, бюджет/лимиты сериализуются в
мок-словарь kv (как это делает bot/main.py через kv_state).
"""
import os
import sys

# детерминированные цены/бюджет ДО обращения к модулям (читаются из env в рантайме)
os.environ["DEEPSEEK_PRICE_IN"] = "0.001"     # USD за 1К токенов входа
os.environ["DEEPSEEK_PRICE_OUT"] = "0.002"    # USD за 1К токенов выхода
os.environ["AI_DAILY_BUDGET_USD"] = "1.0"     # маленький бюджет — легко «исчерпать»
os.environ["AI_DAILY_PER_NPC"] = "30"

from ai import llmlog, cost, textguard, provider, npc_ai, god
from engine import chronicle

_passed = 0
_failed = 0


def check(msg, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"✓ {msg}")
    else:
        _failed += 1
        print(f"✗ {msg}")


DAY = 1_700_000_000.0            # некий момент «сегодня»
NEXT_DAY = DAY + 86_400.0        # ровно следующие сутки


# ───────────────────── 1. llmlog: буфер + оценка стоимости ─────────────────────
def test_llmlog_buffer_and_cost():
    llmlog.flush()   # сброс общего буфера модуля
    # cost = tokens_in/1000*PRICE_IN + tokens_out/1000*PRICE_OUT
    #      = 1000/1000*0.001 + 500/1000*0.002 = 0.001 + 0.001 = 0.002
    row = llmlog.record("deepseek", "deepseek-chat", "mid", 120, 1000, 500,
                        "ok", "npc", "npc-v3")
    check("record() возвращает cost_est по прайсу (0.002)",
          abs(row["cost_est"] - 0.002) < 1e-9)
    check("record() сохранил context/version",
          row["context"] == "npc" and row["version"] == "npc-v3")
    check("буфер содержит одну запись", llmlog.buffered_count() == 1)

    llmlog.record("deepseek", "deepseek-chat", "mid", 90, 2000, 0,
                  "ok", "god", "god-v2")   # +0.002
    check("buffered_cost() суммирует окно (0.004)",
          abs(llmlog.buffered_cost() - 0.004) < 1e-9)

    # неизвестный outcome чинится к 'error', телеметрия не падает
    r3 = llmlog.record("deepseek", "deepseek-chat", "mid", 1, 0, 0,
                       "ЛОЛ", "npc", "")
    check("неизвестный outcome чинится к error", r3["outcome"] == "error")

    drained = llmlog.flush()
    check("flush() отдал весь батч (3 записи)", len(drained) == 3)
    check("flush() очистил буфер", llmlog.buffered_count() == 0)
    check("buffered_cost() после flush = 0", llmlog.buffered_cost() == 0.0)

    # неизвестная модель -> стоимость 0, но запись есть (не роняем)
    r4 = llmlog.record("x", "unknown-model", "cheap", 5, 100, 100, "ok", "npc", "")
    check("неизвестная модель -> cost 0.0", r4["cost_est"] == 0.0)
    llmlog.flush()


# ───────────────────── 2. BudgetGuard: порог, сброс суток, персист ─────────────────────
def test_budget_guard_threshold_and_reset():
    g = cost.BudgetGuard()
    g.load(None, now=DAY)   # чистый старт
    check("свежий guard: расход 0", g.spent_today(DAY) == 0.0)
    check("бюджет из env = 1.0 USD", abs(cost.daily_budget_usd() - 1.0) < 1e-9)

    g.add(0.4, now=DAY)
    check("расход < лимита -> НЕ исчерпан", not g.exhausted(DAY))
    g.add(0.4, now=DAY)     # итого 0.8
    check("0.8 < 1.0 -> НЕ исчерпан", not g.exhausted(DAY))
    g.add(0.3, now=DAY)     # итого 1.1 >= 1.0
    check("расход >= лимита -> ИСЧЕРПАН", g.exhausted(DAY))
    check("spent_today отражает сумму (~1.1)", abs(g.spent_today(DAY) - 1.1) < 1e-9)

    # смена суток обнуляет расход и снимает исчерпание
    check("следующий день -> расход 0", g.spent_today(NEXT_DAY) == 0.0)
    check("следующий день -> НЕ исчерпан", not g.exhausted(NEXT_DAY))


def test_budget_guard_persist():
    kv = {}   # мок kv_state
    g1 = cost.BudgetGuard()
    g1.load(None, now=DAY)
    g1.add(0.6, now=DAY)
    kv["llm_spend"] = g1.snapshot(DAY)          # bot/main.py: db.kv_set("llm_spend", ...)
    check("снимок содержит дату и расход",
          kv["llm_spend"]["date"] and abs(kv["llm_spend"]["usd"] - 0.6) < 1e-9)

    g2 = cost.BudgetGuard()                      # «после рестарта»
    g2.load(kv["llm_spend"], now=DAY)
    check("персист: расход восстановлен (0.6)", abs(g2.spent_today(DAY) - 0.6) < 1e-9)

    # снимок из прошлого дня -> сегодня расход нулевой (день сменился)
    g3 = cost.BudgetGuard()
    g3.load(kv["llm_spend"], now=NEXT_DAY)
    check("персист из прошлого дня -> расход 0 сегодня",
          g3.spent_today(NEXT_DAY) == 0.0)


# ───────────────────── 3. provider.enabled: мок бюджета + кэш 60с ─────────────────────
class _FakeGuard:
    def __init__(self, over):
        self.over = over
    def exhausted(self, now=None):
        return self.over
    def spent_today(self, now=None):
        return 99.0 if self.over else 0.0


def test_provider_enabled_budget_gate_and_cache():
    os.environ["AI_PROVIDER"] = "deepseek"
    os.environ["DEEPSEEK_API_KEY"] = "sk-test"
    provider.set_runtime(None)                  # следовать окружению + сбросить кэш
    orig_guard = cost.BUDGET_GUARD

    try:
        # бюджет НЕ исчерпан -> enabled True
        cost.BUDGET_GUARD = _FakeGuard(over=False)
        provider.set_runtime(None)              # сброс кэша enabled
        check("бюджет свободен -> enabled True", provider.enabled(now=DAY) is True)

        # теперь «исчерпываем» бюджет, но в пределах 60с кэш держит старое True
        cost.BUDGET_GUARD = _FakeGuard(over=True)
        check("кэш 60с: в пределах окна всё ещё True",
              provider.enabled(now=DAY + 30) is True)
        # за пределами TTL пересчёт -> False (аварийное отключение)
        check("после 60с пересчёт -> False (бюджет исчерпан)",
              provider.enabled(now=DAY + 61) is False)
        check("budget_exhausted() проксирует guard", provider.budget_exhausted(DAY) is True)

        # ручной kill-switch выключает мгновенно (сброс кэша внутри set_runtime)
        cost.BUDGET_GUARD = _FakeGuard(over=False)
        provider.set_runtime(False)
        check("kill-switch OFF -> enabled False сразу", provider.enabled(now=DAY) is False)
        check("runtime_state() == False после выключения", provider.runtime_state() is False)
        provider.set_runtime(None)
        check("kill-switch возврат -> enabled True", provider.enabled(now=DAY) is True)
    finally:
        cost.BUDGET_GUARD = orig_guard
        provider.set_runtime(None)


# ───────────────────── 4. TokenBucket: круговой export/import ─────────────────────
def test_token_bucket_roundtrip():
    b = cost.TokenBucket()
    b.record(1, "npc_a", now=DAY)
    b.record(1, "npc_a", now=DAY)
    b.record(2, "npc_b", now=DAY)
    check("used() считает обращения пары", b.used(1, "npc_a", DAY) == 2)

    snap = b.export_state(now=DAY)
    b2 = cost.TokenBucket()
    b2.import_state(snap, now=DAY)
    check("import восстановил счётчик пары (2)", b2.used(1, "npc_a", DAY) == 2)
    check("import восстановил вторую пару (1)", b2.used(2, "npc_b", DAY) == 1)

    # снимок из прошлого дня игнорируется (лимиты и так обнулены)
    b3 = cost.TokenBucket()
    b3.import_state(snap, now=NEXT_DAY)
    check("import из прошлого дня -> счётчики пусты", b3.used(1, "npc_a", NEXT_DAY) == 0)


# ───────────────────── 5. textguard.sanitize_out ─────────────────────
def test_sanitize_out():
    check("вырезает markdown-инъекции (* _ ` [ ])",
          textguard.sanitize_out("при*вет_ `[x]`") == "привет x")
    check("схлопывает пробелы", textguard.sanitize_out("а   б\t\nв") == "а б в")
    check("вырезает управляющие символы",
          "\x00" not in textguard.sanitize_out("а\x00б"))
    long = textguard.sanitize_out("я" * 100, max_len=10)
    check("обрезает до max_len с многоточием", len(long) == 10 and long.endswith("…"))
    check("пустой/None -> пустая строка",
          textguard.sanitize_out("") == "" and textguard.sanitize_out(None) == "")
    check("keep_newlines сохраняет абзацы",
          "\n" in textguard.sanitize_out("абзац1\n\nабзац2", keep_newlines=True))


# ───────────────────── 6. Версии промптов непустые ─────────────────────
def test_prompt_versions():
    check("npc_ai.PROMPT_VERSION непуст", bool(npc_ai.PROMPT_VERSION))
    check("god.PROMPT_VERSION непуст", bool(god.PROMPT_VERSION))
    check("версии различаются (npc vs god)",
          npc_ai.PROMPT_VERSION != god.PROMPT_VERSION)


# ───────────────────── 7. Фишка 10 минут: newcomer один раз ─────────────────────
def test_chronicle_newcomer_once():
    chronicle.reset()
    chronicle.record_once("newcomer", "42", "Герой ступил(а) на туманные улицы Брода")
    chronicle.record_once("newcomer", "42", "Герой ступил(а) на туманные улицы Брода")
    recent = chronicle.recent(10)
    hits = [t for t in recent if "туманные улицы" in t]
    check("newcomer пишется РОВНО один раз на игрока", len(hits) == 1)
    # другой игрок -> отдельная запись
    chronicle.record_once("newcomer", "99", "Другой ступил(а) на туманные улицы Брода")
    check("другой uid -> отдельное событие", len(chronicle.recent(10)) == 2)
    chronicle.reset()


if __name__ == "__main__":
    test_llmlog_buffer_and_cost()
    test_budget_guard_threshold_and_reset()
    test_budget_guard_persist()
    test_provider_enabled_budget_gate_and_cache()
    test_token_bucket_roundtrip()
    test_sanitize_out()
    test_prompt_versions()
    test_chronicle_newcomer_once()
    print(f"\nИТОГО: ✅ {_passed} пройдено, ❌ {_failed} провалено")
    if _failed:
        sys.exit(1)
    print("=== llm budget OK ===")
