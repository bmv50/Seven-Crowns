# -*- coding: utf-8 -*-
"""
Этап 9: тесты деплой-артефактов и fail-fast валидации окружения.

Лёгкие, БЕЗ Docker / Postgres / aiogram: парсим compose как YAML, вычитываем
Dockerfile/.dockerignore/скрипты как текст, а env-валидацию гоняем через чистую
функцию bot.config_check.check_config. Живой `docker build` и бэкап на реальной
БД в песочнице не запускаются — это за владельцем (см. docs/DEPLOY.md).
"""
from __future__ import annotations

import os
import sys

import yaml

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_passed = 0
_failed = 0


def check(name: str, cond: bool):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✅ {name}")
    else:
        _failed += 1
        print(f"  ❌ {name}")


def _read(*parts: str) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


# ───────── docker-compose.yml ─────────
print("docker-compose.yml:")
compose_raw = _read("docker-compose.yml")
compose = yaml.safe_load(compose_raw)
check("парсится как YAML и содержит services", isinstance(compose, dict) and "services" in compose)
svc = compose.get("services", {})
check("сервис postgres присутствует", "postgres" in svc)
check("сервис bot присутствует", "bot" in svc)
check("сервис backup присутствует", "backup" in svc)
check("postgres имеет healthcheck (pg_isready)",
      "healthcheck" in svc.get("postgres", {})
      and "pg_isready" in str(svc["postgres"]["healthcheck"]))
check("bot зависит от healthy postgres",
      str(svc.get("bot", {}).get("depends_on", {})).find("service_healthy") >= 0)
check("bot собирается из Dockerfile (build)", "build" in svc.get("bot", {}))
check("bot использует env_file .env",
      ".env" in str(svc.get("bot", {}).get("env_file", "")))
check("bot имеет restart-политику", bool(svc.get("bot", {}).get("restart")))
check("порт Postgres НЕ проброшен наружу", "ports" not in svc.get("postgres", {}))
check("bot не публикует порты", "ports" not in svc.get("bot", {}))
check("backup монтирует ./backups", "/backups" in str(svc.get("backup", {}).get("volumes", "")))
check("backup делает pg_dump в цикле", "pg_dump" in str(svc.get("backup", {})))
check("backup чистит дампы старше 14 дней",
      "-mtime +14" in str(svc.get("backup", {})))
check("объявлен том pgdata", "pgdata" in (compose.get("volumes") or {}))

# ───────── Dockerfile ─────────
print("Dockerfile:")
dockerfile = _read("Dockerfile")
check("базовый образ python:3.12-slim", "python:3.12-slim" in dockerfile)
check("создаётся non-root пользователь (useradd)", "useradd" in dockerfile)
check("переключение на non-root (USER)", "\nUSER " in dockerfile or dockerfile.startswith("USER "))
check("есть HEALTHCHECK", "HEALTHCHECK" in dockerfile)
check("HEALTHCHECK проверяет heartbeat-файл", "mud_heartbeat" in dockerfile)
check("ENV PROD=1 в образе", "PROD=1" in dockerfile)
check("ENV LOG_JSON=1 в образе", "LOG_JSON=1" in dockerfile)
check("ENV PYTHONUNBUFFERED=1", "PYTHONUNBUFFERED=1" in dockerfile)
check("CMD запускает bot.main", 'bot.main' in dockerfile and "CMD" in dockerfile)
check("устанавливает зависимости с constraints", "constraints.txt" in dockerfile)
check("НЕ копирует тесты в образ (нет COPY test_)", "COPY test_" not in dockerfile)

# ───────── .dockerignore ─────────
print(".dockerignore:")
dockerignore = _read(".dockerignore")
di_lines = {l.strip() for l in dockerignore.splitlines()}
check(".env исключён", ".env" in di_lines)
check("TeleMud/ исключён", any("TeleMud" in l for l in di_lines))
check("тесты исключены (test_*.py)", any(l.startswith("test_") for l in di_lines))
check("images/ исключены", any("images" in l for l in di_lines))
check(".git исключён", any(l == ".git" or l.startswith(".git") for l in di_lines))

# ───────── env-валидация (fail-fast) ─────────
print("env-валидация (bot.config_check):")
from bot import config_check as C

r = C.check_config({"BOT_TOKEN": "ВСТАВЬ_СВОЙ_ТОКЕН"})
check("заглушка BOT_TOKEN → отказ", (not r.ok) and bool(r.errors))

r = C.check_config({"BOT_TOKEN": "PASTE_YOUR_TOKEN_HERE"})
check("англ. заглушка BOT_TOKEN → отказ", not r.ok)

r = C.check_config({"BOT_TOKEN": "", })
check("пустой BOT_TOKEN → отказ", not r.ok)

r = C.check_config({"BOT_TOKEN": "123456:realtoken", "PROD": "1"})
check("PROD=1 без DATABASE_URL → отказ", not r.ok)

r = C.check_config({"BOT_TOKEN": "123456:realtoken", "PROD": "1",
                    "DATABASE_URL": "postgresql://mud:pass@postgres:5432/mud"})
check("PROD=1 + валидный DATABASE_URL → ok", r.ok)

r = C.check_config({"BOT_TOKEN": "123456:realtoken", "PROD": "0"})
check("dev без DATABASE_URL → ok, но с предупреждением", r.ok and bool(r.warnings))

r = C.check_config({"BOT_TOKEN": "123456:realtoken",
                    "DATABASE_URL": "x", "ADMIN_IDS": "12,foo,34"})
check("мусор в ADMIN_IDS → предупреждение (не фатал)",
      r.ok and any("foo" in w for w in r.warnings))

good, bad = C.parse_admin_ids("12, 34 ;foo")
check("parse_admin_ids разделяет валидные и мусор", good == {12, 34} and bad == ["foo"])

# ───────── скрипты бэкапа/восстановления ─────────
print("scripts/*.sh:")
backup_sh = _read("scripts", "backup.sh")
restore_sh = _read("scripts", "restore.sh")
restore_test_sh = _read("scripts", "restore_test.sh")
check("backup.sh существует и зовёт pg_dump -Fc", "pg_dump -Fc" in backup_sh)
check("backup.sh ротирует дампы (14 дней)", "-mtime +14" in backup_sh)
check("restore.sh требует подтверждения YES", 'YES' in restore_sh and "read" in restore_sh)
check("restore.sh делает dropdb/createdb/pg_restore",
      "dropdb" in restore_sh and "createdb" in restore_sh and "pg_restore" in restore_sh)
check("restore_test.sh поднимает временную БД и pg_restore",
      "createdb" in restore_test_sh and "pg_restore" in restore_test_sh)
check("restore_test.sh проверяет characters/kv_state/economy_ledger",
      "characters" in restore_test_sh and "kv_state" in restore_test_sh
      and "economy_ledger" in restore_test_sh)
check("restore_test.sh дропает временную БД (cleanup)", "trap cleanup" in restore_test_sh)

# ───────── итог ─────────
total = _passed + _failed
print()
print(f"ИТОГО: ✅ {_passed} пройдено, ❌ {_failed} провалено (из {total})")
sys.exit(1 if _failed else 0)
