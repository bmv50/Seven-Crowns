#!/bin/sh
# СЕМЬ КОРОН — ручной бэкап БД (Этап 9). POSIX sh, работает в WSL/Linux.
# Снимает pg_dump -Fc (custom-формат, сжатый, пригоден для pg_restore) по
# DATABASE_URL в каталог backups/ и ротирует дампы старше 14 дней.
#
# Использование:
#     DATABASE_URL=postgresql://mud:pass@localhost:5432/mud sh scripts/backup.sh
set -eu

: "${DATABASE_URL:?DATABASE_URL не задан (postgresql://user:pass@host:port/db)}"
BACKUP_DIR="${BACKUP_DIR:-backups}"
mkdir -p "$BACKUP_DIR"

TS=$(date -u +%Y%m%d_%H%M%S)
OUT="$BACKUP_DIR/mud_${TS}.dump"

echo "pg_dump -Fc -> $OUT"
pg_dump -Fc -d "$DATABASE_URL" -f "$OUT"
echo "OK: $OUT ($(du -h "$OUT" 2>/dev/null | cut -f1))"

# Ротация: удалить дампы старше 14 дней (регламент хранения).
find "$BACKUP_DIR" -name 'mud_*.dump' -mtime +14 -delete 2>/dev/null || true
