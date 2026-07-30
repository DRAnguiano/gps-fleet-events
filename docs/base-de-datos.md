# Modelo de datos

Definición ejecutable: [`sql/01_schema.sql`](../sql/01_schema.sql) y [`sql/02_vistas_operativas.sql`](../sql/02_vistas_operativas.sql). Ambos se ejecutan solos la primera vez que se crea el volumen de Postgres.

## Tabla `gps_event`

| Columna | Tipo | Descripción |
|---|---|---|
| `id` | `BIGSERIAL` | Llave primaria |
| `unit_code` | `TEXT NOT NULL` | Unidad tal como la nombra el proveedor (`T-142`, `V MOVIL 4`) |
| `event_time` | `TIMESTAMPTZ NOT NULL` | Momento del evento; del cuerpo del correo, o del header `Date` si no viene |
| `type` | `TEXT NOT NULL` | Ver catálogo en [parser-correo.md](parser-correo.md#catálogo-de-tipos-de-evento) |
| `geofence_name` | `TEXT` | Texto de ubicación reportado |
| `geofence_kind` | `TEXT` | `CASETA` · `BASE` · `PLANTA` · `GASOLINERA` · `OTHER` |
| `speed_kmh` | `NUMERIC` | Velocidad reportada, si la alerta la trae |
| `raw_subject` | `TEXT` | Asunto original |
| `raw_body` | `TEXT` | Cuerpo original — permite reprocesar sin volver a IMAP |
| `data` | `JSONB NOT NULL` | Campos variables según tipo de evento |
| `source_hash` | `TEXT NOT NULL` | SHA-256 del contenido normalizado; llave de idempotencia |
| `created_at` | `TIMESTAMPTZ NOT NULL` | Cuándo se insertó (distinto de cuándo ocurrió) |

`event_time` vs `created_at` importa: la diferencia entre ambos es el retraso de la ingesta. Un salto grande delata que el correo llegó tarde o que se está corriendo un backfill.

### Contenido típico de `data`

```jsonc
{
  "address_text": "CARR. TORREON-SALTILLO KM 32",
  "geofence_kind": "OTHER",
  "fuel_liters": 85.5,          // FUEL_FILL / FUEL_DRAIN: litros del movimiento
  "fuel_level_liters": 240.0,   // FUEL_LEVEL: nivel del tanque
  "odometer_km": 812345,        // MILEAGE
  "parsed_speed_kmh": 0,
  "connection_event": "LOST",   // LOST | RESTORED
  "connection_minutes": 30,
  "email_message_id": "<abc@proveedor>",
  "imap_uid": 4711,
  "email_date": "2026-03-14T18:23:10.000Z"
}
```

Todas las claves pueden faltar. Al consultarlas, usa `->>` con `COALESCE` o filtra antes por `type`.

## Índices

| Índice | Para qué |
|---|---|
| `ux_gps_event_source_hash` (único) | Idempotencia — es lo que hace posible el `ON CONFLICT` |
| `ix_gps_event_unit_time` | "Último estado de la unidad X" — la consulta dominante |
| `ix_gps_event_type_time` | Ventanas por tipo de evento (combustible, cortes de conexión) |
| `ix_gps_event_data` (GIN) | Búsquedas dentro del payload variable |

## Inserción

Tanto n8n como el backfill usan el mismo upsert (`upsertGpsEvent()` en `scripts/backfill_gps_event.js`):

```sql
INSERT INTO gps_event (
  unit_code, event_time, type, geofence_name, speed_kmh,
  raw_subject, raw_body, source_hash, geofence_kind, data
)
VALUES ($1, $2::timestamptz, $3, $4, $5, $6, $7, $8, $9, $10::jsonb)
ON CONFLICT (source_hash) DO UPDATE SET
  unit_code = EXCLUDED.unit_code,
  event_time = EXCLUDED.event_time,
  type = EXCLUDED.type,
  geofence_name = EXCLUDED.geofence_name,
  speed_kmh = EXCLUDED.speed_kmh,
  raw_subject = EXCLUDED.raw_subject,
  raw_body = EXCLUDED.raw_body,
  geofence_kind = EXCLUDED.geofence_kind,
  data = EXCLUDED.data
RETURNING id;
```

`DO UPDATE` y no `DO NOTHING`: así una mejora en el parser se refleja al reprocesar el histórico.

## Vistas

| Vista | Responde |
|---|---|
| `v_unidad_ultimo_evento` | ¿Cuál fue la última señal de cada unidad? |
| `v_unidad_ultimo_movimiento` | ¿Cuándo se movió de verdad por última vez? |
| `v_unidad_estatus` | ¿En qué estado está, desde hace cuánto, y qué tan fresco es el dato? |
| `v_combustible_diario` | Litros cargados/descargados y odómetro por unidad y día |
| `v_cortes_conexion` | Historial de pérdidas y restablecimientos de señal |

Los umbrales de negocio están explicados en [reglas-negocio.md](reglas-negocio.md).

## Consultas de uso frecuente

**Tablero de Tráfico — quién está disponible:**

```sql
SELECT unit_code, estatus, horas_sin_moverse, geofence_name
FROM v_unidad_estatus
ORDER BY
  CASE estatus
    WHEN 'EN_BASE' THEN 1
    WHEN 'EN_ZONA_LAGUNA' THEN 2
    WHEN 'PARADA_EN_RUTA' THEN 3
    ELSE 4
  END,
  horas_sin_moverse DESC;
```

**Unidades sin señal o sin reportar desde hace más de 2 horas:**

```sql
SELECT unit_code, estatus, horas_sin_reporte, geofence_name
FROM v_unidad_estatus
WHERE estatus = 'SIN_SENAL'
   OR horas_sin_reporte > 2
ORDER BY horas_sin_reporte DESC;
```

**Descargas de combustible del último mes, con contexto:**

```sql
SELECT unit_code, event_time, (data->>'fuel_liters')::numeric AS litros,
       geofence_name, geofence_kind
FROM gps_event
WHERE type = 'FUEL_DRAIN'
  AND event_time >= now() - INTERVAL '30 days'
ORDER BY litros DESC NULLS LAST;
```

**Salud de la ingesta — retraso entre evento y carga:**

```sql
SELECT date_trunc('day', created_at) AS dia,
       COUNT(*) AS eventos,
       COUNT(*) FILTER (WHERE type = 'OTHER') AS sin_clasificar,
       ROUND(AVG(EXTRACT(EPOCH FROM (created_at - event_time)) / 60.0)) AS retraso_min
FROM gps_event
WHERE created_at >= now() - INTERVAL '14 days'
GROUP BY dia
ORDER BY dia DESC;
```

## Conexión desde Power BI

Conector **PostgreSQL**, host del servidor, puerto `5432`, base `gpsdb`. Importar las vistas, no la tabla: `gps_event` incluye `raw_body`, que multiplica el tamaño del modelo sin aportar nada al tablero.

Se recomienda un usuario de solo lectura:

```sql
CREATE USER powerbi WITH PASSWORD 'cambia_esto';
GRANT CONNECT ON DATABASE gpsdb TO powerbi;
GRANT USAGE ON SCHEMA public TO powerbi;
GRANT SELECT ON v_unidad_estatus, v_combustible_diario, v_cortes_conexion TO powerbi;
```

## Crecimiento

`raw_body` es lo que hace pesada la tabla. Con volúmenes altos y a partir de cierta antigüedad, se puede liberar espacio sin perder el evento:

```sql
UPDATE gps_event
SET raw_body = NULL
WHERE event_time < now() - INTERVAL '18 months'
  AND raw_body IS NOT NULL;
```

Se pierde la capacidad de reprocesar esos eventos desde la base — pero los correos siguen en Gmail.
