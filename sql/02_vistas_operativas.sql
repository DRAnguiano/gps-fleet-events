-- sql/02_vistas_operativas.sql
--
-- Capa de lectura sobre gps_event: traduce eventos sueltos a las preguntas que
-- Tráfico hace de verdad ("¿esta unidad ya está libre?", "¿lleva mucho parada?",
-- "¿cuánto combustible cargó ayer?").
--
-- PLANTILLA PARAMETRIZABLE. Los umbrales de abajo son los que se usaron en la
-- operación original; ajústalos a tu flota antes de confiar en el resultado.
-- Ver docs/reglas-negocio.md para el porqué de cada regla.
--
--   Umbral de movimiento .......... 5 km/h  (por debajo se considera detenida)
--   Ventana de "reciente" ......... 60 min  (sin eventos nuevos = sin lectura)
--   Detención larga ............... 8 h     (candidata a "operador en casa")
--
-- Se ejecuta al inicializar la base, después de 01_schema.sql.

-- Los nombres de lugar llegan con acentos y en mayúsculas/minúsculas mezcladas
-- ("TORREÓN, COAH.", "Gomez Palacio"); unaccent permite compararlos sin sorpresas.
CREATE EXTENSION IF NOT EXISTS unaccent;

-- ---------------------------------------------------------------------------
-- Último evento conocido por unidad.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_unidad_ultimo_evento AS
SELECT DISTINCT ON (unit_code)
  unit_code,
  event_time      AS ultimo_evento_at,
  type            AS ultimo_tipo,
  geofence_name,
  geofence_kind,
  speed_kmh,
  data
FROM gps_event
ORDER BY unit_code, event_time DESC;

-- ---------------------------------------------------------------------------
-- Último momento en que la unidad se reportó en movimiento real.
-- Es la base para "¿hace cuánto que no se mueve?".
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_unidad_ultimo_movimiento AS
SELECT DISTINCT ON (unit_code)
  unit_code,
  event_time AS ultimo_movimiento_at,
  speed_kmh,
  geofence_name,
  geofence_kind
FROM gps_event
WHERE speed_kmh IS NOT NULL
  AND speed_kmh >= 5           -- umbral de movimiento
ORDER BY unit_code, event_time DESC;

-- ---------------------------------------------------------------------------
-- Estatus operativo consolidado.
--
-- Distingue explícitamente los tres tipos de "parada" que en el correo se ven
-- iguales pero significan cosas distintas para Tráfico:
--   EN_BASE          -> patio o base: la unidad está disponible.
--   EN_ZONA_LAGUNA   -> área metropolitana de La Laguna: probable domicilio
--                       del operador (las unidades se quedan con el operador).
--   PARADA_EN_RUTA   -> detenida fuera de zona conocida: atoramiento, caseta,
--                       descanso o incidente. NO es disponibilidad.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_unidad_estatus AS
WITH base AS (
  SELECT
    e.unit_code,
    e.ultimo_evento_at,
    e.ultimo_tipo,
    e.geofence_name,
    e.geofence_kind,
    e.speed_kmh,
    m.ultimo_movimiento_at,
    -- Área metropolitana de La Laguna: Torreón, Gómez Palacio, Lerdo, Matamoros.
    (lower(unaccent(COALESCE(e.geofence_name, ''))) ~ '(torreon|gomez palacio|lerdo|matamoros|la laguna)')
      AS en_zona_laguna
  FROM v_unidad_ultimo_evento e
  LEFT JOIN v_unidad_ultimo_movimiento m USING (unit_code)
)
SELECT
  unit_code,
  CASE
    WHEN ultimo_tipo = 'CONNECTION_LOST'                        THEN 'SIN_SENAL'
    WHEN ultimo_evento_at < now() - INTERVAL '60 minutes'
     AND ultimo_tipo <> 'CONNECTION_RESTORED'                   THEN 'SIN_LECTURA_RECIENTE'
    WHEN speed_kmh >= 5                                         THEN 'EN_MOVIMIENTO'
    WHEN geofence_kind = 'BASE'                                 THEN 'EN_BASE'
    WHEN en_zona_laguna                                         THEN 'EN_ZONA_LAGUNA'
    ELSE                                                             'PARADA_EN_RUTA'
  END AS estatus,
  ultimo_evento_at,
  ultimo_movimiento_at,
  -- Cuánto lleva sin moverse: lo que Tráfico usa para decidir el siguiente viaje.
  ROUND(
    EXTRACT(EPOCH FROM (now() - COALESCE(ultimo_movimiento_at, ultimo_evento_at))) / 3600.0,
    1
  ) AS horas_sin_moverse,
  (COALESCE(ultimo_movimiento_at, ultimo_evento_at) < now() - INTERVAL '8 hours')
    AS detencion_larga,
  geofence_name,
  geofence_kind,
  speed_kmh
FROM base;

-- ---------------------------------------------------------------------------
-- Combustible por unidad y día: insumo directo del tablero de Power BI.
-- FUEL_FILL y FUEL_DRAIN traen litros en data->>'fuel_liters'.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_combustible_diario AS
SELECT
  unit_code,
  (event_time AT TIME ZONE 'America/Monterrey')::date AS dia,
  COUNT(*) FILTER (WHERE type = 'FUEL_FILL')                       AS eventos_llenado,
  COUNT(*) FILTER (WHERE type = 'FUEL_DRAIN')                      AS eventos_descarga,
  COALESCE(SUM((data->>'fuel_liters')::numeric)
           FILTER (WHERE type = 'FUEL_FILL'), 0)                   AS litros_cargados,
  COALESCE(SUM((data->>'fuel_liters')::numeric)
           FILTER (WHERE type = 'FUEL_DRAIN'), 0)                  AS litros_descargados,
  MAX((data->>'odometer_km')::numeric)                             AS odometro_max_km,
  MIN((data->>'odometer_km')::numeric)                             AS odometro_min_km
FROM gps_event
WHERE type IN ('FUEL_FILL', 'FUEL_DRAIN', 'FUEL_LEVEL', 'MILEAGE')
GROUP BY unit_code, dia;

-- ---------------------------------------------------------------------------
-- Cortes de conexión: minutos sin señal declarados por el proveedor.
-- Sirve tanto para operación como para reclamar SLA al proveedor de monitoreo.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_cortes_conexion AS
SELECT
  unit_code,
  event_time,
  data->>'connection_event'                AS evento,
  (data->>'connection_minutes')::numeric   AS minutos_declarados,
  geofence_name,
  raw_subject
FROM gps_event
WHERE type IN ('CONNECTION_LOST', 'CONNECTION_RESTORED')
ORDER BY event_time DESC;
