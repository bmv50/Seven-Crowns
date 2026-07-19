#!/bin/sh
# СЕМЬ КОРОН — ОБЯЗАТЕЛЬНЫЙ restore-test (Этап 9). Проверяет, что бэкап реально
# восстанавливается: поднимает ВРЕМЕННУЮ базу, льёт в неё последний дамп,
# выполняет проверочные SELECT'ы и удаляет временную базу. Боевую БД не трогает.
# exit 0 — восстановление и проверки прошли; exit 1 — проблема.
#
# Использование:
#     DATABASE_URL=postgresql://mud:pass@localhost:5432/mud sh scripts/restore_test.sh [dump-file]
set -eu

: "${DATABASE_URL:?DATABASE_URL не задан}"
BACKUP_DIR="${BACKUP_DIR:-backups}"
TESTDB="${RESTORE_TEST_DB:-mud_restore_test}"

# Дамп: аргумент или самый свежий из backups/.
DUMP="${1:-}"
[ -n "$DUMP" ] || DUMP=$(ls -t "$BACKUP_DIR"/mud_*.dump 2>/dev/null | head -n1 || true)
[ -n "$DUMP" ] && [ -f "$DUMP" ] || { echo "❌ нет дампа для restore-test (backups/mud_*.dump)"; exit 1; }

BASEURL=$(printf '%s' "$DATABASE_URL" | sed -E 's#(.*/)[^/?]+(\?.*)?$#\1postgres\2#')
TESTURL=$(printf '%s' "$DATABASE_URL" | sed -E "s#(.*/)[^/?]+(\\?.*)?\$#\\1$TESTDB\\2#")

cleanup() { dropdb --if-exists --maintenance-db="$BASEURL" "$TESTDB" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM

echo "restore-test: '$DUMP' -> временная БД '$TESTDB'"
dropdb --if-exists --maintenance-db="$BASEURL" "$TESTDB"
createdb --maintenance-db="$BASEURL" "$TESTDB"
pg_restore -d "$TESTURL" "$DUMP"

# Проверочные SELECT'ы (таблицы существуют и читаются; целостность ledger — суммой).
CHARS=$(psql -tA "$TESTURL" -c "SELECT count(*) FROM characters;")
KV=$(psql -tA "$TESTURL" -c "SELECT count(*) FROM kv_state;")
LEDGER=$(psql -tA "$TESTURL" -c "SELECT count(*) FROM economy_ledger;")
LEDGER_SUM=$(psql -tA "$TESTURL" -c "SELECT COALESCE(sum(amount),0) FROM economy_ledger;")
echo "  characters=$CHARS  kv_state=$KV  economy_ledger.rows=$LEDGER  ledger.sum=$LEDGER_SUM"

fail=0
case "$CHARS"      in ''|*[!0-9]*) echo "❌ таблица characters недоступна"; fail=1;; esac
case "$KV"         in ''|*[!0-9]*) echo "❌ таблица kv_state недоступна"; fail=1;; esac
case "$LEDGER"     in ''|*[!0-9]*) echo "❌ таблица economy_ledger недоступна"; fail=1;; esac
# ledger.sum должен быть числом (знак не проверяем: минт/сток не обязаны быть zero-sum).
case "$LEDGER_SUM" in ''|*[!0-9-]*) echo "❌ сумма economy_ledger нечисловая"; fail=1;; esac

if [ "$fail" -ne 0 ]; then
    echo "❌ restore-test ПРОВАЛЕН"
    exit 1
fi
echo "✅ restore-test OK (дамп восстанавливается и читается)"
exit 0
