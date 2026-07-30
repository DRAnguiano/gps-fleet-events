# Parser de correo (`shared/parseGpsEmail.js`)

Convierte una alerta del proveedor de GPS en una fila normalizada de `gps_event`. Es un módulo sin estado ni dependencias de red: recibe un objeto con el correo ya parseado en MIME y devuelve el evento.

```js
const { parseGpsEmail } = require('./shared/parseGpsEmail');

const evento = parseGpsEmail({
  subject: 'Descarga de Combustible (UNID T-142)',
  textPlain: '...',
  textHtml: '...',
  mailDate: new Date(),
  email_message_id: '<abc@proveedor>',
  imap_uid: 4711,
});
```

## Entrada

| Campo | Origen | Notas |
|---|---|---|
| `subject` | asunto del correo | Lleva casi siempre la unidad y el tipo de evento |
| `textPlain` / `text` | cuerpo en texto plano | Preferido |
| `textHtml` | cuerpo HTML | Se limpia con `stripHtml()` si no hay texto plano |
| `mailDate` / `date` | header `Date` | Respaldo cuando el cuerpo no trae fecha |
| `email_message_id` | header `Message-ID` | Entra en el hash de idempotencia |
| `imap_uid` | UID del mensaje | Trazabilidad hacia el buzón |

## Salida

```jsonc
{
  "unit_code": "T-142",
  "event_time": "2026-03-14T18:22:07Z",
  "event_type": "FUEL_DRAIN",
  "geofence_name": "CARR. TORREON-SALTILLO KM 32",
  "geofence_kind": "OTHER",
  "speed_kmh": 0,
  "fuel_liters": 85.5,
  "raw_subject": "...",
  "raw_body": "...",
  "hash_input": "T-142|2026-03-14T18:22:07Z|FUEL_DRAIN|...",
  "source_hash": "9f2c...",
  "parse_ok": true,
  "data": {
    "address_text": "CARR. TORREON-SALTILLO KM 32",
    "geofence_kind": "OTHER",
    "fuel_level_liters": 240.0,
    "odometer_km": 812345,
    "parsed_speed_kmh": 0,
    "connection_event": null,
    "connection_minutes": null,
    "fuel_liters": 85.5,
    "email_message_id": "<abc@proveedor>",
    "imap_uid": 4711,
    "email_date": "2026-03-14T18:23:10.000Z"
  }
}
```

**`parse_ok` es `true` solo si se detectaron unidad y hora.** Sin esos dos campos el evento no es utilizable: el flujo de n8n lo desvía a revisión manual y el backfill lo cuenta como rechazado (`rejectCount`) pero marca el correo como leído, para no atorarse en el mismo mensaje.

## Catálogo de tipos de evento

| `event_type` | Se detecta por | Campos que aporta |
|---|---|---|
| `FUEL_DRAIN` | "descarga de combustible" | `fuel_liters` |
| `FUEL_FILL` | "llenado de combustible" | `fuel_liters` |
| `FUEL_LEVEL` | asunto "sensor de combustible" + valor en litros | `fuel_level_liters` |
| `MILEAGE` | asunto "mileage sensor" + valor en km | `odometer_km` |
| `IDLE` | "inactividad" o "detenido" | `speed_kmh` |
| `CASETA_ENTER` | "cruce de caseta", "entró en caseta" | `geofence_name` |
| `CONNECTION_LOST` | "pérdida de conexión", "conexión perdida" | `connection_minutes` |
| `CONNECTION_RESTORED` | "se restablece conexión", "conexión restaurada" | `connection_minutes` |
| `OTHER` | ninguna regla aplicó | — |

Los eventos de conexión se evalúan **primero**: un asunto puede mencionar combustible y conexión a la vez, y para la operación el corte de señal domina —si no hay señal, lo demás no es confiable.

`OTHER` no es un error. Es una alerta real que aún no tiene regla. Vale la pena revisar periódicamente qué asuntos caen ahí:

```sql
SELECT raw_subject, COUNT(*)
FROM gps_event
WHERE type = 'OTHER'
GROUP BY raw_subject
ORDER BY 2 DESC
LIMIT 30;
```

## Normalización

**Texto.** `normalize()` pasa a minúsculas y quita acentos (NFD + descarte de diacríticos). El proveedor escribe indistintamente "Pérdida de Conexión" y "PERDIDA DE CONEXION"; sin esto, la mitad de los eventos caería en `OTHER`.

**Fechas.** El cuerpo usa `DD.MM.YYYY HH:MM:SS`, que JavaScript no parsea de forma nativa y confiable (`14.03.2026` se interpreta distinto según entorno). `toIsoFromDDMMYYYY_HHMMSS()` lo convierte explícitamente a ISO. Si el cuerpo no trae fecha, se usa el header `Date`, que es cuándo llegó el correo y no cuándo ocurrió el evento — una aproximación aceptable, pero conviene saber que existe.

> El timestamp del cuerpo se interpreta como UTC (se le añade la `Z`). Si tu proveedor reporta en hora local, hay que ajustar esa conversión: es el punto más delicado del parser.

**Números.** `parseNum()` acepta coma o punto como separador decimal; el proveedor mezcla ambos.

**Identificador de unidad.** `pickUnitCode()` prueba en orden los formatos observados: `(UNID T-142)`, `(V MOVIL 4)`, `(TRANSIT ...)`, códigos con guion, y por último patrones sueltos `T###` / `V###`. El orden importa: va de lo más específico a lo más genérico para no capturar un número de caseta como si fuera una unidad.

## Clasificación de geocercas

El proveedor no manda una geocerca estructurada, solo un texto de ubicación ("cerca de ..."). `geofence_kind` se infiere de ese texto:

| `geofence_kind` | Palabras en el texto |
|---|---|
| `CASETA` | caseta |
| `BASE` | base, patio |
| `PLANTA` | planta, cedis |
| `GASOLINERA` | gasolinera, pemex, bp, shell |
| `OTHER` | ninguna |

Es una heurística sobre texto libre, y hay que tratarla como tal: **`BASE` es la que más pesa en las decisiones** (implica unidad disponible), así que conviene verificar cómo nombra tu proveedor a los patios antes de confiar en la clasificación. Sustituir esto por geocercas propias sobre coordenadas es el pendiente más valioso del proyecto.

## Extender el parser

1. Agrega el patrón en la cadena de detección de `event_type`, respetando el orden de prioridad.
2. Si el evento trae un dato nuevo, ponlo dentro de `data` — no agregues columnas salvo que vayas a filtrar por ese campo de forma constante.
3. Si ese dato debe distinguir dos eventos como distintos, agrégalo también a `hash_input`.
4. Reprocesa el histórico desde la propia base (no hace falta volver a IMAP: `raw_subject` y `raw_body` están guardados).

> Cambiar `hash_input` cambia el hash de **todos** los eventos futuros. Los ya insertados conservan el hash viejo, así que un reprocesamiento posterior los insertará de nuevo con el hash nuevo. Si modificas el hash, planea una deduplicación o un reprocesamiento completo.
