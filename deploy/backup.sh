#!/usr/bin/env bash
# Daily dump of the carwatch database. Cron example:
#   15 3 * * * /opt/stacks/carwatch/backup.sh >> /var/log/carwatch-backup.log 2>&1
set -euo pipefail
DIR="${CARWATCH_DIR:-/opt/stacks/carwatch}"
OUT="${BACKUP_DIR:-$DIR/backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
mkdir -p "$OUT"
cd "$DIR"
set -a; . ./.env; set +a
file="$OUT/carwatch-$(date +%Y%m%d-%H%M).sql.gz"
docker compose exec -T db pg_dump -U "${POSTGRES_USER:-carwatch}" "${POSTGRES_DB:-carwatch}" | gzip > "$file"
find "$OUT" -name 'carwatch-*.sql.gz' -mtime "+$KEEP_DAYS" -delete
echo "backup ok: $file"
