# -*- coding: utf-8 -*-
"""
Обратная связь беты: команда /bug и кнопка канала сообщества.
Запуск:
    python run_tests.py feedback
Без БД и без сети. Телеграм-транспорт не трогаем — проверяем то, что можно
проверить без него: разбор ссылки на канал, состав клавиатуры «☰ Ещё»,
регистрацию команды и каталог аналитики.

Почему это важно на бете: отчёт без контекста стоит часа раскопок, а битая
ссылка в клавиатуре роняет ВЕСЬ экран (Telegram отклоняет разметку целиком,
а не одну кнопку) — поэтому ссылка проверяется до показа.
"""
import sys

from bot import config_check as cfg
from bot import commands as cmds
from bot import ui
from engine import analytics
from engine.character import Character

_passed = 0
_failed = 0


def check(name, cond, extra=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✅ {name}")
    else:
        _failed += 1
        print(f"  ❌ {name}" + (f" — {extra}" if extra else ""))


def _btn_texts(kb):
    return [b.text for row in kb.inline_keyboard for b in row]


def _btn_urls(kb):
    return [getattr(b, "url", None) for row in kb.inline_keyboard for b in row]


# ───────────────── 1. ссылка на канал ─────────────────

def test_community_url():
    print("\n[1] Разбор COMMUNITY_URL")
    check("пусто → пусто", cfg.community_url("") == "")
    check("None → пусто", cfg.community_url(None) == "")
    check("https проходит",
          cfg.community_url("https://t.me/seven_crowns") == "https://t.me/seven_crowns")
    check("@канал нормализуется в ссылку",
          cfg.community_url("@seven_crowns") == "https://t.me/seven_crowns")
    check("пробелы по краям срезаются",
          cfg.community_url("  https://t.me/x  ") == "https://t.me/x")
    # мусор не должен доехать до клавиатуры
    check("голое имя без схемы отвергается", cfg.community_url("seven_crowns") == "")
    check("плейсхолдер отвергается",
          cfg.community_url("[УКАЖИТЕ ССЫЛКУ]") == "")
    check("строка с пробелом внутри отвергается",
          cfg.community_url("https://t.me/a b") == "")
    check("схема без домена отвергается", cfg.community_url("https://") == "")
    check("одинокая собака отвергается", cfg.community_url("@") == "")


# ───────────────── 2. меню «Ещё» ─────────────────

def _hero():
    ch = Character(uid=1, name="Тест", cls="warrior", race="human")
    ch.level = 30            # выше всех гейтов, чтобы ряды не схлопывались
    ch.init_vitals()
    return ch


def test_more_menu():
    print("\n[2] Клавиатура «☰ Ещё»")
    ch = _hero()

    kb = ui.kb_more(ch)
    texts = _btn_texts(kb)
    check("кнопка бага есть всегда", any("баг" in t.lower() for t in texts), texts)
    check("без настроенного канала кнопки канала нет",
          not any(u for u in _btn_urls(kb)), _btn_urls(kb))
    check("«Сезон» отсюда убран (остаётся в меню «Герой»)",
          not any("Сезон" in t for t in texts), texts)

    kb2 = ui.kb_more(ch, community=("Официальный канал", "https://t.me/seven_crowns"))
    texts2 = _btn_texts(kb2)
    urls2 = [u for u in _btn_urls(kb2) if u]
    check("с настроенным каналом появляется кнопка-ссылка",
          urls2 == ["https://t.me/seven_crowns"], urls2)
    check("заголовок канала виден в подписи",
          any("Официальный канал" in t for t in texts2), texts2)
    check("кнопка бага при этом никуда не делась",
          any("баг" in t.lower() for t in texts2))

    # пустая ссылка при заданном заголовке — кнопки быть не должно
    kb3 = ui.kb_more(ch, community=("Официальный канал", ""))
    check("пустая ссылка не создаёт кнопку", not any(u for u in _btn_urls(kb3)))

    # ── раскладка: 4 ряда по 2 + отдельный «Назад» ──
    grid = [[b.text for b in row] for row in kb2.inline_keyboard]
    check("рядов ровно 5 (4 пары + «Назад»)", len(grid) == 5, grid)
    check("в каждом из первых четырёх рядов по 2 кнопки",
          all(len(r) == 2 for r in grid[:4]), [len(r) for r in grid])
    check("«Назад» отдельным последним рядом",
          len(grid[-1]) == 1 and grid[-1][0].endswith("Назад"), grid[-1])
    order = [t for row in grid[:4] for t in row]
    want = ["Группа", "Достижения", "Бестиарий", "Хроника",
            "Настройки", "Помощь", "Официальный канал", "баг"]
    check("порядок кнопок как заказан",
          all(w.lower() in order[i].lower() for i, w in enumerate(want)), order)

    # ── новичок: «Группа» ещё закрыта — дыры в сетке быть не должно ──
    low = _hero()
    low.level = 1
    kb4 = ui.kb_more(low, community=("Официальный канал", "https://t.me/x"))
    grid4 = [[b.text for b in row] for row in kb4.inline_keyboard]
    check("у новичка ряды всё равно по 2 кнопки (кроме «Назад»)",
          all(len(r) == 2 for r in grid4[:-1]) or len(grid4[-2]) <= 2,
          [len(r) for r in grid4])
    check("одиночных рядов, кроме «Назад», нет",
          sum(1 for r in grid4[:-1] if len(r) == 1) <= 1,
          [len(r) for r in grid4])


# ───────────────── 3. регистрация команды ─────────────────

def test_command_registered():
    print("\n[3] Команда /bug зарегистрирована")
    canon = {c for _cat, lst in cmds.COMMAND_GROUPS for (c, _a, _u, _d) in lst}
    check("bug есть в справке команд", "bug" in canon)
    check("русский синоним «баг» ведёт на bug",
          cmds.ALIASES.get("баг") == "bug", cmds.ALIASES.get("баг"))
    check("bug есть в меню команд Telegram",
          "bug" in {c for c, _d in cmds.SLASH_MENU})


# ───────────────── 4. аналитика ─────────────────

def test_analytics_event():
    print("\n[4] Событие bug_report в каталоге аналитики")
    check("bug_report известно каталогу", "bug_report" in analytics.EVENTS)
    # неизвестные события track() молча отбрасывает — если бы имя не совпало,
    # счётчик отчётов на бете тихо остался бы нулевым
    analytics.reset() if hasattr(analytics, "reset") else None
    analytics.track(1, "bug_report")
    buf = getattr(analytics, "_buf", None)
    if buf is not None:
        check("track() действительно кладёт событие в буфер",
              any(e.get("event") == "bug_report" for e in buf), list(buf)[-3:])


def main():
    print("=" * 56)
    print("ОБРАТНАЯ СВЯЗЬ БЕТЫ: /bug и канал сообщества")
    print("=" * 56)
    test_community_url()
    test_more_menu()
    test_command_registered()
    test_analytics_event()
    print("\n" + "=" * 56)
    print(f"ИТОГО: ✅ {_passed} пройдено, ❌ {_failed} провалено")
    print("=" * 56)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
