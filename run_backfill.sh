#!/bin/bash

MAX_TOTAL=6882
BATCH=5
BLOCK=100
COOLDOWN=60

processed=0

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker no está disponible en este shell."
  echo "Abre una terminal normal de Ubuntu/WSL y ejecuta el script desde ahí."
  exit 1
fi

while [ $processed -lt $MAX_TOTAL ]
do
  echo "=============================="
  echo "PROCESADOS HASTA AHORA: $processed"
  echo "=============================="

  block_count=0

  while [ $block_count -lt $BLOCK ] && [ $processed -lt $MAX_TOTAL ]
  do
    echo "Procesando lote..."

    docker run --rm \
      --network bot_gps_operativo_default \
      --env-file .env \
      -e PGHOST=postgres \
      -e PGPORT=5432 \
      -e PGDATABASE=gpsdb \
      -e PGUSER=gps \
      -e PGPASSWORD=gps_pass_cambia_esto \
      -v "$(pwd)/shared:/app/shared" \
      -v "$(pwd)/scripts:/app/scripts" \
      -v "$(pwd)/package.json:/app/package.json" \
      -w /app \
      node:20-bookworm \
      sh -c "npm install && node /app/scripts/backfill_gps_event.js"

    status=$?

    if [ $status -eq 0 ]; then
      processed=$((processed + BATCH))
      block_count=$((block_count + BATCH))
      echo "Lote OK. Total procesados: $processed"
    else
      echo "ERROR: el lote falló con código $status"
      echo "Se detiene el script para no seguir contando falso."
      exit $status
    fi

    sleep 3
  done

  echo ""
  echo "===== BLOQUE DE $BLOCK COMPLETADO ====="
  echo "Cooling down $COOLDOWN segundos..."
  echo ""

  sleep $COOLDOWN
done

echo "=============================="
echo "BACKFILL COMPLETADO"
echo "TOTAL PROCESADO: $processed"
echo "=============================="
