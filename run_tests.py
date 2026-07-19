#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Единый раннер тестов проекта.

Находит все test_*.py в корне репозитория и прогоняет каждый отдельным
подпроцессом (sys.executable) — падение или утечка состояния в одном файле
не валит остальные и не портит общий отчёт.

Запуск:
    python run_tests.py           # все test_*.py
    python run_tests.py --quick   # пропустить файлы из SLOW_TESTS

Выход: 0, если все файлы завершились с кодом 0, иначе 1.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Файлы, пропускаемые при --quick. Сейчас пусто — все test_*.py в проекте
# быстрые (секунды); список заведён на будущее для по-настоящему долгих
# sim-подобных прогонов, если такие тесты появятся.
SLOW_TESTS: set[str] = set()


def _reconfigure_stdio() -> None:
    """На Windows консоль часто в cp1251/cp866 — переключаем на UTF-8,
    чтобы русские сообщения тестов не роняли раннер UnicodeEncodeError."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def discover_tests() -> list[Path]:
    return sorted(ROOT.glob("test_*.py"))


def last_meaningful_line(output: str) -> str:
    """Последняя содержательная строка вывода теста: приоритет — строке с
    «ИТОГО» (сводка) или начинающейся с OK, иначе — просто последняя непустая."""
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    if not lines:
        return ""
    for ln in reversed(lines):
        upper = ln.upper()
        if "ИТОГО" in upper or upper.startswith("OK"):
            return ln
    return lines[-1]


def run_one(path: Path, env: dict) -> dict:
    start = time.monotonic()
    proc = subprocess.run(
        [sys.executable, str(path)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    duration = time.monotonic() - start
    combined = f"{proc.stdout}\n{proc.stderr}"
    return {
        "file": path.name,
        "code": proc.returncode,
        "summary": last_meaningful_line(combined),
        "duration": duration,
    }


def main() -> int:
    _reconfigure_stdio()
    quick = "--quick" in sys.argv[1:]

    tests = discover_tests()
    if quick:
        tests = [t for t in tests if t.name not in SLOW_TESTS]

    if not tests:
        print("run_tests: файлы test_*.py не найдены в корне репозитория.")
        return 1

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    results = []
    for path in tests:
        print(f"-> {path.name} ...", flush=True)
        result = run_one(path, env)
        results.append(result)
        status = "OK" if result["code"] == 0 else f"FAIL(code={result['code']})"
        print(f"   {status}  {result['duration']:.2f}s  {result['summary']}")

    col_file, col_status, col_time = 38, 16, 9
    line_width = col_file + col_status + col_time + 4

    print()
    print("=" * line_width)
    print(f"{'Файл':<{col_file}} {'Статус':<{col_status}} {'Время, с':>{col_time}}  Итог")
    print("-" * line_width)
    failed = 0
    total_time = 0.0
    for r in results:
        ok = r["code"] == 0
        if not ok:
            failed += 1
        total_time += r["duration"]
        status = "OK" if ok else f"FAIL(code={r['code']})"
        print(f"{r['file']:<{col_file}} {status:<{col_status}} {r['duration']:>{col_time}.2f}  {r['summary'][:60]}")
    print("-" * line_width)
    print(f"Всего файлов: {len(results)}, упало: {failed}, суммарное время: {total_time:.2f}с")
    print("=" * line_width)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
