-- sql/01_schema.sql
-- Esquema base de la ingesta de eventos GPS.
-- Postgres lo ejecuta automáticamente al inicializar el volumen de datos
-- (se monta en /docker-entrypoint-initdb.d desde docker-compose.yml).

CREATE TABLE IF NOT EXISTS gps_event (
  id            BIGSERIAL PRIMARY KEY,

  -- Identificador de la unidad tal como aparece en la alerta (T123, V MOVIL 4, ...).
  unit_code     TEXT NOT NULL,

  -- Momento del evento. Sale del cuerpo del correo; si no viene, del header Date.
  event_time    TIMESTAMPTZ NOT NULL,

  -- Catálogo del parser: FUEL_DRAIN, FUEL_FILL, FUEL_LEVEL, MILEAGE, IDLE,
  -- CASETA_ENTER, CONNECTION_LOST, CONNECTION_RESTORED, OTHER.
  type          TEXT NOT NULL,

  -- Lugar reportado en la alerta y su clasificación
  -- (CASETA, BASE, PLANTA, GASOLINERA, OTHER).
  geofence_name TEXT,
  geofence_kind TEXT,

  speed_kmh     NUMERIC,

  -- Correo original: permite reprocesar sin volver a IMAP.
  raw_subject   TEXT,
  raw_body      TEXT,

  -- Campos variables del parser: odómetro, litros, minutos sin conexión,
  -- message-id, uid IMAP, etc. Ver docs/parser-correo.md.
  data          JSONB NOT NULL DEFAULT '{}'::jsonb,

  -- SHA-256 del contenido normalizado. Es la llave de idempotencia:
  -- reprocesar el mismo correo actualiza la fila en vez de duplicarla.
  source_hash   TEXT NOT NULL,

  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Compatibilidad hacia atrás: bases creadas con el esquema inicial
-- (sin geofence_kind ni data) se actualizan sin perder información.
ALTER TABLE gps_event ADD COLUMN IF NOT EXISTS geofence_kind TEXT;
ALTER TABLE gps_event ADD COLUMN IF NOT EXISTS data JSONB NOT NULL DEFAULT '{}'::jsonb;

-- Idempotencia del backfill y del flujo de n8n (ON CONFLICT (source_hash)).
CREATE UNIQUE INDEX IF NOT EXISTS ux_gps_event_source_hash
ON gps_event (source_hash);

-- Consulta dominante: "último estado de la unidad X".
CREATE INDEX IF NOT EXISTS ix_gps_event_unit_time
ON gps_event (unit_code, event_time DESC);

-- Filtros por tipo de evento en ventanas de tiempo (combustible, conexión).
CREATE INDEX IF NOT EXISTS ix_gps_event_type_time
ON gps_event (type, event_time DESC);

-- Búsquedas dentro del payload variable.
CREATE INDEX IF NOT EXISTS ix_gps_event_data
ON gps_event USING GIN (data);
