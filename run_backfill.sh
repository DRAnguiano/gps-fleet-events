#!/bin/bash

set -euo pipefail

# Tope de correos a procesar en toda la corrida.
MAX_TOTAL="${MAX_TOTAL:-6882}"
# Correos por bloque antes de pausar (evita rate limit de Gmail).
BLOCK="${BLOCK:-100}"
# Pausa entre bloques, en segundos.
COOLDOWN="${COOLDOWN:-60}"

# Red de Docker donde vive el contenedor de Postgres.
DOCKER_NETWORK="${DOCKER_NETWORK:-bot_gps_operativo_default}"

processed=0

# Credenciales: se leen del .env, nunca se escriben en el script.
if [ ! -f .env ]; then
  echo "ERROR: no existe .env en $(pwd). Copia .env.example y complétalo."
  exit 1
fi

: "${PGPASSWORD:=$(grep -E '^PGPASSWORD=' .env | tail -n1 | cut -d= -f2-)}"

if [ -z "${PGPASSWORD}" ]; then
  echo "ERROR: PGPASSWORD no está definido (ni en el entorno ni en .env)."
  exit 1
fi

LOCKFILE=/tmp/backfill_gps.lock

if [ -f "$LOCKFILE" ]; then
  echo "ERROR: ya hay un backfill corriendo."
  exit 1
fi

trap 'rm -f "$LOCKFILE"' EXIT
touch "$LOCKFILE"

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker no está disponible en este shell."
  echo "Abre una terminal normal de Ubuntu/WSL y ejecuta el script desde ahí."
  exit 1
fi

while [ "$processed" -lt "$MAX_TOTAL" ]
do
  echo "=============================="
  echo "PROGRESO REAL HASTA AHORA: $processed"
  echo "=============================="

  block_count=0

  while [ "$block_count" -lt "$BLOCK" ] && [ "$processed" -lt "$MAX_TOTAL" ]
  do
    echo "Procesando lote..."

        set +e
    output=$(docker run --rm \
      --network "$DOCKER_NETWORK" \
      --env-file .env \
      -e PGHOST="${PGHOST:-postgres}" \
      -e PGPORT="${PGPORT:-5432}" \
      -e PGDATABASE="${PGDATABASE:-gpsdb}" \
      -e PGUSER="${PGUSER:-gps}" \
      -e PGPASSWORD="$PGPASSWORD" \
      -v "$(pwd)/shared:/app/shared" \
      -v "$(pwd)/scripts:/app/scripts" \
      -v "$(pwd)/package.json:/app/package.json" \
      -w /app \
      node:20-bookworm \
      sh -c "npm install --silent && node /app/scripts/backfill_gps_event.js" \
      2>&1)
    status=$?
    set -e

    echo "$output"

    summary_line=$(echo "$output" | grep 'FINAL_SUMMARY=' | tail -n1 || true)

    if [ -z "$summary_line" ]; then
      echo "ERROR: no se encontró FINAL_SUMMARY en la salida."
      exit 1
    fi

    summary_json="${summary_line#FINAL_SUMMARY=}"

    ok_count=$(echo "$summary_json" | sed -n 's/.*"okCount":\([0-9]\+\).*/\1/p')
    reject_count=$(echo "$summary_json" | sed -n 's/.*"rejectCount":\([0-9]\+\).*/\1/p')
    fetched_count=$(echo "$summary_json" | sed -n 's/.*"fetchedCount":\([0-9]\+\).*/\1/p')
    seen_count=$(echo "$summary_json" | sed -n 's/.*"seenMarkedCount":\([0-9]\+\).*/\1/p')

    ok_count=${ok_count:-0}
    reject_count=${reject_count:-0}
    fetched_count=${fetched_count:-0}
    seen_count=${seen_count:-0}

    echo "Resumen lote -> fetched=$fetched_count ok=$ok_count reject=$reject_count seen=$seen_count status=$status"

    if [ "$status" -eq 0 ]; then
      processed=$((processed + ok_count))
      block_count=$((block_count + ok_count))
      echo "Lote OK. Total procesados reales: $processed"
    elif [ "$status" -eq 2 ]; then
      echo "No hubo más backlog útil o el lote no produjo inserciones válidas."
      echo "Se detiene el backfill."
      exit 0
    else
      echo "ERROR: el lote falló con código $status"
      exit "$status"
    fi

    sleep 3
  done

  echo ""
  echo "===== BLOQUE COMPLETADO ====="
  echo "Cooling down $COOLDOWN segundos..."
  echo ""

  sleep "$COOLDOWN"
done

echo "=============================="
echo "BACKFILL COMPLETADO"
echo "TOTAL PROCESADO REAL: $processed"
echo "=============================="