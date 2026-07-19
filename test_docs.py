# -*- coding: utf-8 -*-
"""
Этап 10: лёгкие тесты пользовательских документов-черновиков (PRIVACY/TERMS/
RULES/REFUNDS) и связанных команд бота (/delete_me, /privacy, /terms,
/support). БЕЗ Docker/Postgres/aiogram-рантайма — только текстовые проверки
файлов, тем же духом, что test_deploy.py.

Что проверяем (грубо, не юридическая экспертиза — только гигиена черновика):
  • все 4 документа существуют и начинаются с плашки «ЧЕРНОВИК»;
  • в каждом есть хотя бы один плейсхолдер [УКАЖИТЕ ...];
  • документы не заявляют СОБЛЮДЕНИЕ конкретных законов/режимов как факт
    (напр. «152-ФЗ», «GDPR-compliant») — juridически это должен решить юрист,
    а не наш черновик; упоминание GDPR в духе «может применяться» — разрешено;
  • PRIVACY.md содержит отдельный раздел про передачу текста внешнему ИИ
    (DeepSeek) и ссылается на /delete_me для прав пользователя;
  • RULES.md содержит жалобу/лестницу мер/апелляцию;
  • REFUNDS.md упоминает /paysupport;
  • bot/main.py: команды /delete_me, /privacy, /terms существуют (grep по
    исходнику, без импорта aiogram/бота).
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_passed = 0
_failed = 0


def check(name: str, cond: bool):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  OK {name}")
    else:
        _failed += 1
        print(f"  FAIL {name}")


def _read(*parts: str) -> str:
    path = os.path.join(ROOT, *parts)
    with open(path, encoding="utf-8") as f:
        return f.read()


DOC_FILES = ["PRIVACY.md", "TERMS.md", "RULES.md", "REFUNDS.md"]

# Строки, которых НЕ должно быть как самостоятельного юридического ЗАЯВЛЕНИЯ
# (мы не выдумываем номера законов и не заявляем формальное соответствие).
FORBIDDEN_LAW_CLAIMS = [
    "152-фз",
    "gdpr-compliant",
    "соответствует gdpr",
    "полностью соответствует",
]

print("Документы docs/legal/*.md существуют:")
docs = {}
for fname in DOC_FILES:
    path = os.path.join("docs", "legal", fname)
    exists = os.path.isfile(os.path.join(ROOT, path))
    check(f"{path} существует", exists)
    if exists:
        docs[fname] = _read(path)

print("Плашка «ЧЕРНОВИК» в начале каждого документа:")
for fname in DOC_FILES:
    text = docs.get(fname, "")
    head = text[:400]
    check(f"{fname}: плашка ЧЕРНОВИК в первых 400 символах",
          "ЧЕРНОВИК" in head)

print("Плейсхолдеры [УКАЖИТЕ ...] присутствуют:")
for fname in DOC_FILES:
    text = docs.get(fname, "")
    check(f"{fname}: содержит хотя бы один плейсхолдер [УКАЖИТЕ",
          "[УКАЖИТЕ" in text)

print("Нет утверждений о соответствии конкретным законам/режимам:")
for fname in DOC_FILES:
    low = docs.get(fname, "").lower()
    bad = [c for c in FORBIDDEN_LAW_CLAIMS if c in low]
    check(f"{fname}: без заявлений вида {FORBIDDEN_LAW_CLAIMS}", not bad)

print("PRIVACY.md — раздел про внешний ИИ (DeepSeek):")
privacy = docs.get("PRIVACY.md", "")
check("PRIVACY.md упоминает DeepSeek", "DeepSeek" in privacy)
check("PRIVACY.md содержит отдельный раздел про передачу данных внешнему ИИ",
      "внешнему ИИ-провайдеру" in privacy or "внешнему ИИ" in privacy)
check("PRIVACY.md ссылается на /delete_me (права пользователя)",
      "/delete_me" in privacy)
check("PRIVACY.md проговаривает отсутствие паролей/платёжных данных",
      "паролей" in privacy.lower() or "пароли" in privacy.lower())

print("RULES.md — жалобы и лестница мер:")
rules = docs.get("RULES.md", "")
check("RULES.md упоминает кнопку «⚠️ Пожаловаться»", "Пожаловаться" in rules)
check("RULES.md описывает лестницу: предупреждение", "редупреждение" in rules)
check("RULES.md описывает лестницу: мут", "Мут" in rules or "мут" in rules)
check("RULES.md описывает лестницу: бан", "Бан" in rules or "бан" in rules)
check("RULES.md упоминает апелляцию через /support", "/support" in rules)
check("RULES.md запрещает мультиаккаунт-абьюз рефералки",
      "мультиаккаунт" in rules.lower())

print("REFUNDS.md — /paysupport:")
refunds = docs.get("REFUNDS.md", "")
check("REFUNDS.md упоминает /paysupport", "/paysupport" in refunds)
check("REFUNDS.md отмечает отсутствие вывода средств",
      "не подлежат обмену" in refunds or "не подлежит обмену" in refunds)

print("TERMS.md — базовые пункты оферты:")
terms = docs.get("TERMS.md", "")
check("TERMS.md упоминает Telegram Stars", "Telegram Stars" in terms)
check("TERMS.md содержит возрастной плейсхолдер", "[УКАЖИТЕ" in terms and
      ("12+" in terms or "16+" in terms or "возраст" in terms.lower()))
check("TERMS.md ссылается на RULES.md (недопустимое поведение)",
      "RULES.md" in terms)

print("Команды бота (bot/main.py, grep по исходнику):")
main_src = _read("bot", "main.py")
check('bot/main.py: Command("delete_me")', 'Command("delete_me")' in main_src)
check('bot/main.py: Command("privacy")', 'Command("privacy")' in main_src)
check('bot/main.py: Command("terms")', 'Command("terms")' in main_src)
check('bot/main.py: Command("support")', 'Command("support")' in main_src)
check("bot/main.py: экспорт данных (export_data callback)",
      '"export_data"' in main_src)
check("bot/main.py: /delete_me переиспользует cmd_reset (не дублирует удаление)",
      "await cmd_reset(message)" in main_src)

print()
print(f"ИТОГО: {_passed} пройдено, {_failed} провалено")
sys.exit(1 if _failed else 0)
