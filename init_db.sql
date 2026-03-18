-- init_db.sql
-- Ejecutado automáticamente al inicializar la imagen Postgres

-- Extensión opcional para UUIDs (descomenta si la necesitas)
-- CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS gps_event (
  id BIGSERIAL PRIMARY KEY,
  unit_code TEXT NOT NULL,
  event_time TIMESTAMPTZ NOT NULL,
  type TEXT NOT NULL,
  geofence_name TEXT,
  speed_kmh NUMERIC,
  raw_subject TEXT,
  raw_body TEXT,
  source_hash TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_gps_event_source_hash
ON gps_event (source_hash);

CREATE INDEX IF NOT EXISTS ix_gps_event_unit_time
ON gps_event (unit_code, event_time DESC);
