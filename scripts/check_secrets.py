#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скан секретов по репозиторию перед закрытой бетой.

Ищет в файлах репо характерные паттерны боевых секретов (токены Telegram,
ключи OpenAI/DeepSeek, ключи Google/Gemini, DSN Postgres с паролем,
URL прокси с логином:паролем) и падает с exit(1), если что-то нашёл.

В отчёт (stdout) выводится ТОЛЬКО "путь:строка:тип_находки" — само значение
секрета никогда не печатается.

Запуск:
    python scripts/check_secrets.py [путь_к_репо]

Если путь не указан — берётся корень репозитория (родитель scripts/).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# --- Каталоги/файлы, которые не сканируем целиком -------------------------
EXCLUDED_DIR_NAMES = {
    ".git",
    ".pytest_cache",
}
# Относительные (от корня репо) пути-исключения (файлы или директории)
EXCLUDED_RELATIVE_PATHS = {
    ".env",
    "graphify-out/cache",
}


def is_excluded_deployments_json(rel_posix: str) -> bool:
    """TeleMud/deployments/*.json — намеренно исключены из скана (рантайм-отчёты Go-референса)."""
    parts = rel_posix.split("/")
    return (
        len(parts) == 3
        and parts[0] == "TeleMud"
        and parts[1] == "deployments"
        and parts[2].endswith(".json")
    )


def is_excluded(rel_path: Path) -> bool:
    rel_posix = rel_path.as_posix()
    for excl in EXCLUDED_RELATIVE_PATHS:
        if rel_posix == excl or rel_posix.startswith(excl + "/"):
            return True
    for part in rel_path.parts:
        if part in EXCLUDED_DIR_NAMES:
            return True
    if is_excluded_deployments_json(rel_posix):
        return True
    return False


# --- Паттерны секретов ------------------------------------------------------
# Каждый паттерн: (человекочитаемое имя, скомпилированный regex)
PATTERNS: list[tuple[str, re.Pattern]] = [
    ("telegram-bot-token", re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_-]{30,}\b")),
    ("openai-style-sk-key", re.compile(r"\bsk-[a-zA-Z0-9]{20,}\b")),
    ("google-aiza-key", re.compile(r"\bAIza[A-Za-z0-9_-]{30,}\b")),
    (
        "postgres-dsn-with-password",
        re.compile(r"\bpostgres(?:ql)?://[\w.-]+:[^@\s]+@"),
    ),
    (
        "proxy-url-with-password",
        re.compile(r"\bhttps?://[\w.-]+:[^@\s]+@"),
    ),
]

# Известные локальные заглушки, на которые не нужно ругаться
# (postgres(ql)?://mud:mudpass@... — локальный dev-DSN из .env.example)
SAFE_POSTGRES_CREDENTIALS = {("mud", "mudpass")}

# Общеупотребимые слова-плейсхолдеры в примерах DSN/прокси (документация, README,
# дефолты в чужом Go-коде TeleMud/) — не являются настоящими секретами.
PLACEHOLDER_WORDS = {
    "user", "username", "login", "admin", "postgres",
    "password", "pass", "pwd", "secret", "changeme", "example", "xxx",
}

_CRED_RE = re.compile(r"[a-zA-Z][\w+.-]*://([^:/\s@]+):([^@\s]+)@([^/\s]+)")


def _looks_like_placeholder(text: str) -> bool:
    if not text:
        return False
    if "ВСТАВЬ_СВОЙ" in text.upper():
        return True
    if text.lower() in PLACEHOLDER_WORDS:
        return True
    # Слово из одних заглавных букв (кириллица/латиница), напр. ПОЛЬЗОВАТЕЛЬ,
    # ПАРОЛЬ, ЛОГИН — формат документации, а не настоящий секрет.
    letters_only = text.replace("_", "")
    if letters_only.isalpha() and letters_only.isupper() and len(letters_only) >= 4:
        return True
    return False


def _is_safe_credential_match(line: str, match: re.Match) -> bool:
    """Проверяет DSN/URL-находку (postgres или прокси) на локальную/документационную заглушку."""
    m = _CRED_RE.search(line, match.start())
    if not m:
        return False
    user, password, host = m.group(1), m.group(2), m.group(3)
    host = host.split(":", 1)[0].rstrip(".-/")  # отбросить :порт и хвостовую пунктуацию
    if (user, password) in SAFE_POSTGRES_CREDENTIALS and host in ("localhost", "127.0.0.1", "db", "postgres"):
        return True
    return _looks_like_placeholder(user) or _looks_like_placeholder(password)


BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".bmp",
    ".zip", ".gz", ".tar", ".7z", ".rar",
    ".pdf", ".exe", ".dll", ".so", ".pyc", ".pyo",
    ".ttf", ".otf", ".woff", ".woff2",
    ".db", ".sqlite", ".sqlite3",
}


def looks_binary(path: Path) -> bool:
    if path.suffix.lower() in BINARY_EXTENSIONS:
        return True
    try:
        with path.open("rb") as f:
            chunk = f.read(4096)
        return b"\x00" in chunk
    except OSError:
        return True


def scan_file(path: Path) -> list[tuple[int, str]]:
    """Возвращает список (номер_строки, тип_находки) для одного файла."""
    findings: list[tuple[int, str]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return findings

    url_kinds = {"postgres-dsn-with-password", "proxy-url-with-password"}
    for lineno, line in enumerate(text.splitlines(), start=1):
        for name, pattern in PATTERNS:
            for match in pattern.finditer(line):
                if name in url_kinds and _is_safe_credential_match(line, match):
                    continue
                findings.append((lineno, name))
    return findings


def iter_candidate_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if is_excluded(rel):
            continue
        if looks_binary(path):
            continue
        yield path


def main() -> int:
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]

    all_findings: list[tuple[str, int, str]] = []
    for path in iter_candidate_files(root):
        rel = path.relative_to(root).as_posix()
        for lineno, kind in scan_file(path):
            all_findings.append((rel, lineno, kind))

    if not all_findings:
        print("check_secrets: находок нет, всё чисто.")
        return 0

    print("check_secrets: НАЙДЕНЫ возможные секреты (значения скрыты):")
    for rel, lineno, kind in sorted(all_findings):
        print(f"{rel}:{lineno}:{kind}")
    print(f"\nВсего находок: {len(all_findings)}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
