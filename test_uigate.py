# -*- coding: utf-8 -*-
"""
Тесты прогрессивного UI (Этап 4.2, задача 3): engine/uigate.py.
Без Telegram и без PostgreSQL. Запуск: python3 test_uigate.py

Покрывает:
  • unlocked() — таблично: заперта ниже гейта, открыта на/выше гейта;
  • hint() — формат подсказки;
  • next_unlock_visible() — видна за ≤GATE_PREVIEW уровней до гейта, не видна раньше;
  • здравомыслие таблицы FEATURES (уровни в разумном диапазоне 1..60).
"""
from engine import uigate

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


print("═" * 50)
print("  ТЕСТЫ ПРОГРЕССИВНОГО UI (engine/uigate.py)")
print("═" * 50)

# ─────────────────────── 1. unlocked() — таблично ───────────────────────
print("\n[1] unlocked(): заперта ниже гейта, открыта на/выше гейта")
for _feature, _min_lv in uigate.FEATURES.items():
    check(f"'{_feature}': заперта на 1 уровень ниже гейта ({_min_lv - 1})",
          uigate.unlocked(_feature, _min_lv - 1) is False)
    check(f"'{_feature}': открыта РОВНО на гейте ({_min_lv})",
          uigate.unlocked(_feature, _min_lv) is True)
    check(f"'{_feature}': открыта выше гейта ({_min_lv + 5})",
          uigate.unlocked(_feature, _min_lv + 5) is True)

check("unlocked(): неизвестная фича не гейтится (открыта всегда)",
      uigate.unlocked("несуществующая_фича", 1) is True)
check("unlocked(): уровень 1 запирает все фичи с гейтом >1",
      all(uigate.unlocked(f, 1) is False for f, lv in uigate.FEATURES.items() if lv > 1))
check("unlocked(): уровень 60 (кап) открывает все фичи",
      all(uigate.unlocked(f, 60) is True for f in uigate.FEATURES))

# ─────────────────────── 2. hint() — формат ───────────────────────
print("\n[2] hint(): формат подсказки")
for _feature, _min_lv in uigate.FEATURES.items():
    _h = uigate.hint(_feature)
    check(f"'{_feature}': hint начинается с 🔒", _h.startswith("🔒"))
    check(f"'{_feature}': hint содержит уровень гейта ({_min_lv})", str(_min_lv) in _h)
check("hint(): неизвестная фича не падает и возвращает строку с 🔒",
      isinstance(uigate.hint("несуществующая_фича"), str)
      and uigate.hint("несуществующая_фича").startswith("🔒"))

# ─────────────────────── 3. next_unlock_visible() ───────────────────────
print("\n[3] next_unlock_visible(): видна за ≤GATE_PREVIEW уровней, не видна раньше")
check(f"GATE_PREVIEW сконфигурирован (>0)", uigate.GATE_PREVIEW > 0)
for _feature, _min_lv in uigate.FEATURES.items():
    _edge = _min_lv - uigate.GATE_PREVIEW      # ровно на границе окна предпросмотра
    _before = _edge - 1                        # на 1 уровень раньше границы — ещё не видна
    if _edge >= 1:
        check(f"'{_feature}': видна заблокированной на границе окна (ур.{_edge})",
              uigate.next_unlock_visible(_feature, _edge) is True)
    if _before >= 1:
        check(f"'{_feature}': НЕ видна на 1 уровень раньше границы (ур.{_before})",
              uigate.next_unlock_visible(_feature, _before) is False)
    check(f"'{_feature}': не показывается заблокированной, когда уже открыта (ур.{_min_lv})",
          uigate.next_unlock_visible(_feature, _min_lv) is False)
    check(f"'{_feature}': не показывается заблокированной сильно выше гейта (ур.{_min_lv + 20})",
          uigate.next_unlock_visible(_feature, _min_lv + 20) is False)

check("next_unlock_visible(): неизвестная фича никогда не 'скоро откроется'",
      uigate.next_unlock_visible("несуществующая_фича", 1) is False)

# перебор всех уровней 1..15 для talents (min_level=3) — ручная сверка окна
_talents_lv = uigate.FEATURES["talents"]
for _lv in range(1, 16):
    _expected_locked_preview = (_lv < _talents_lv) and (_talents_lv - _lv) <= uigate.GATE_PREVIEW
    check(f"talents ур.{_lv}: next_unlock_visible == {_expected_locked_preview}",
          uigate.next_unlock_visible("talents", _lv) == _expected_locked_preview)

# ─────────────────────── 4. Здравомыслие таблицы FEATURES ───────────────────────
print("\n[4] FEATURES: разумные значения уровней")
check("FEATURES не пуста", len(uigate.FEATURES) > 0)
for _feature, _min_lv in uigate.FEATURES.items():
    check(f"'{_feature}': уровень гейта в диапазоне 1..60 (факт {_min_lv})",
          1 <= _min_lv <= 60)
_expected_features = {"talents", "party", "professions", "craft", "daily",
                       "auction", "factions", "guild", "arena", "season"}
check(f"все ожидаемые фичи присутствуют в FEATURES",
      _expected_features.issubset(set(uigate.FEATURES.keys())))
check("бой/журнал/карта/сумка НЕ гейтятся (их нет в FEATURES)",
      not ({"combat", "journal", "map", "inventory", "inv"} & set(uigate.FEATURES.keys())))

# ─────────────────────── ИТОГ ───────────────────────
print("\n" + "═" * 50)
print(f"ИТОГО: ✅ {_passed} пройдено, ❌ {_failed} провалено")
print("═" * 50)
import sys
sys.exit(1 if _failed else 0)
