#!/bin/sh
# СЕМЬ КОРОН — восстановление БД из дампа (Этап 9). ОПАСНАЯ операция:
# целевая база ПОЛНОСТЬЮ пересоздаётся. Двухшаговая защита — требуется явный
# ввод YES. POSIX sh (WSL/Linux).
#
# Использование:
#     DATABASE_URL=postgresql://mud:pass@localhost:5432/mud sh scripts/restore.sh backups/mud_YYYYMMDD_HHMMSS.dump
set -eu

: "${DATABASE_URL:?DATABASE_URL не задан}"
DUMP="${1:-}"
[ -n "$DUMP" ] || { echo "usage: sh scripts/restore.sh <dump-file>"; exit 2; }
[ -f "$DUMP" ] || { echo "нет такого дампа: $DUMP"; exit 2; }

# Имя целевой БД и «служебный» URL к maintenance-базе postgres (для drop/create).
DBNAME=$(printf '%s' "$DATABASE_URL" | sed -E 's#.*/([^/?]+).*#\1#')
BASEURL=$(printf '%s' "$DATABASE_URL" | sed -E 's#(.*/)[^/?]+(\?.*)?$#\1postgres\2#')

echo "⚠️  ВНИМАНИЕ: база '$DBNAME' будет УДАЛЕНА и пересоздана из:"
echo "    $DUMP"
echo "Все текущие данные будут потеряны без возможности отката."
printf 'Для подтверждения наберите YES и нажмите Enter: '
read CONFIRM
[ "$CONFIRM" = "YES" ] || { echo "Отменено (введено не YES)."; exit 1; }

echo "→ dropdb $DBNAME"
dropdb --if-exists --maintenance-db="$BASEURL" "$DBNAME"
echo "→ createdb $DBNAME"
createdb --maintenance-db="$BASEURL" "$DBNAME"
echo "→ pg_restore"
pg_restore -d "$DATABASE_URL" "$DUMP"
echo "✅ Восстановление завершено из $DUMP."
