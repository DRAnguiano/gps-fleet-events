#!/bin/bash
#
# Respaldo diario de la base operativa.
#
# El servidor vive en sitio, sin UPS: un corte de luz puede dejar la base a
# medias. La estrategia es de dos capas —este dump diario, y el buzón de Gmail
# como respaldo de origen (ver scripts/backfill_gps_event.js). Si el dump se
# pierde, los eventos se pueden reconstruir desde los correos.
#
# Uso:
#   ./scripts/backup_db.sh
#
# Cron sugerido (03:00 todos los días):
#   0 3 * * * cd /ruta/al/proyecto && ./scripts/backup_db.sh >> backups/backup.log 2>&1

set -euo pipefail

cd "$(dirname "$0")/.."

BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
CONTAINER="${PG_CONTAINER:-gps_postgres}"
PGDATABASE="${PGDATABASE:-gpsdb}"
PGUSER="${PGUSER:-gps}"

mkdir -p "$BACKUP_DIR"

stamp="$(date +%Y%m%d_%H%M%S)"
outfile="$BACKUP_DIR/gpsdb_${stamp}.sql.gz"

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "ERROR: el contenedor '$CONTAINER' no está corriendo."
  exit 1
fi

echo "[$(date -Is)] Respaldando $PGDATABASE -> $outfile"

# --clean: el dump se puede restaurar sobre una base existente.
docker exec "$CONTAINER" \
  pg_dump --clean --if-exists -U "$PGUSER" -d "$PGDATABASE" \
  | gzip -9 > "$outfile"

size="$(du -h "$outfile" | cut -f1)"
echo "[$(date -Is)] Respaldo OK ($size)"

# Verificación mínima: un dump truncado por corte de energía no debe pasar
# como bueno y desplazar a los respaldos viejos en la rotación.
if ! gzip -t "$outfile"; then
  echo "ERROR: el respaldo quedó corrupto, se elimina y NO se rota."
  rm -f "$outfile"
  exit 1
fi

echo "[$(date -Is)] Eliminando respaldos con más de $RETENTION_DAYS días"
find "$BACKUP_DIR" -name 'gpsdb_*.sql.gz' -type f -mtime +"$RETENTION_DAYS" -print -delete

echo "[$(date -Is)] Listo."

# Restaurar:
#   gunzip -c backups/gpsdb_YYYYMMDD_HHMMSS.sql.gz \
#     | docker exec -i gps_postgres psql -U gps -d gpsdb
