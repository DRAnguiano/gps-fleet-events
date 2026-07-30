# Arquitectura y decisiones de diseño

## El punto de partida

El proveedor de monitoreo GPS no expone API. Lo único programable que ofrece es **el envío de alertas por correo electrónico**: cada vez que una unidad dispara un evento configurado (llenado de combustible, descarga, exceso de velocidad, cruce de caseta, inactividad, pérdida de conexión), llega un correo a una cuenta de la empresa.

De ahí sale la decisión central del proyecto: **tratar el buzón de correo como si fuera el API que no existe.**

Eso trae tres consecuencias que condicionan todo lo demás:

1. **El formato no es un contrato.** Los correos son texto pensado para humanos. El parser debe ser tolerante y, sobre todo, debe fallar de forma visible cuando no reconoce algo, no inventar valores.
2. **La entrega no es confiable ni ordenada.** Un correo puede llegar tarde, duplicado, o no llegar. La ingesta tiene que ser idempotente y reprocesable.
3. **El buzón es la fuente de verdad.** Si la base de datos se pierde, los correos siguen ahí. Eso convierte a Gmail en la última capa de respaldo (ver [operacion.md](operacion.md)).

## Vista general

```
Proveedor GPS
     │ alerta por correo
     ▼
Gmail (IMAP)
     │
     ├──────────────► n8n · flujo en vivo (INBOX)
     │                      │
     └──────────────► backfill / recuperación (carpeta HISTORICAL)
                            │
                     shared/parseGpsEmail.js
                            │ fila normalizada + source_hash
                            ▼
                   PostgreSQL · gps_event
                            │
              ┌─────────────┼──────────────┐
              ▼             ▼              ▼
        vistas SQL      Power BI     FastAPI /ask
       (reglas de      (combustible,  (RAG + Ollama)
        negocio)        km, tiempo)        │
              └────────► n8n ◄─────────────┘
                          │
                    Telegram · Tráfico
```

## Decisiones y por qué

### Un solo parser para los dos caminos

`shared/parseGpsEmail.js` es un módulo CommonJS puro, sin dependencias de red ni de base de datos. Lo usan:

- el nodo *Code* de n8n en el flujo en vivo, y
- `scripts/backfill_gps_event.js` en la reconstrucción histórica.

Si hubiera dos implementaciones, el histórico y el tiempo real divergirían en silencio, y las vistas de negocio mezclarían filas con criterios distintos. Un solo módulo garantiza que el mismo correo produzca la misma fila por cualquiera de los dos caminos.

### Idempotencia por hash de contenido

Cada evento calcula un SHA-256 sobre la concatenación normalizada de sus campos significativos (unidad, hora, tipo, geocerca, velocidad, litros, odómetro, conexión, asunto y message-id). Ese `source_hash` tiene un índice único, y toda inserción es `ON CONFLICT (source_hash) DO UPDATE`.

Esto es lo que permite, sin miedo:

- reprocesar el buzón completo después de un corte de energía,
- volver a correr un lote que falló a la mitad,
- que un correo duplicado por el proveedor no infle las estadísticas.

Se incluye el `message-id` en el hash a propósito: dos alertas genuinamente distintas con el mismo contenido (el proveedor a veces reenvía) se conservan como eventos separados, mientras que el reprocesamiento del *mismo* correo colapsa sobre la misma fila.

### Una tabla ancha con `JSONB`, no un esquema normalizado

`gps_event` mantiene como columnas lo que se consulta y se indexa siempre (unidad, hora, tipo, geocerca, velocidad) y manda a `data JSONB` lo que solo aplica a ciertos tipos de evento (litros, odómetro, minutos sin conexión, uid IMAP).

El motivo es práctico: el catálogo de alertas del proveedor cambia sin aviso. Una alerta nueva con un campo nuevo entra en `data` sin migración. Normalizar habría significado una migración de esquema cada vez que el proveedor agrega un tipo de alerta, sobre un servidor en sitio y sin ventana de mantenimiento.

También se guarda `raw_subject` y `raw_body`: si el parser mejora, se puede reprocesar todo el histórico desde la propia base, sin volver a IMAP.

### Las reglas de negocio viven en SQL, no en el código

Las vistas de `sql/02_vistas_operativas.sql` son la capa que traduce eventos a decisiones. Están en SQL porque sus consumidores son heterogéneos —Power BI, el bot, consultas manuales de Tráfico— y todos deben ver exactamente la misma definición de "unidad disponible". Si la regla viviera en la API, Power BI tendría su propia versión y las dos se irían separando.

Ver [reglas-negocio.md](reglas-negocio.md) para el detalle de cada umbral.

### LLM local

El modelo corre en Ollama dentro del mismo servidor. La información de flota, operadores y pagos no sale de la empresa. El costo es que el modelo es pequeño (`phi3:mini`), lo que se compensa con dos guardarraíles: umbral mínimo de similitud antes de responder (0.60), y un prompt que obliga a responder solo con el contexto recuperado.

### ngrok

Telegram necesita un webhook público y el servidor está detrás del NAT de la empresa, sin IP fija. ngrok con dominio reservado resuelve eso sin tocar la red corporativa. Es una dependencia externa consciente: si ngrok cae, el bot deja de responder, pero la ingesta y la base siguen funcionando.

## Flujo de ingesta en n8n

> El JSON exportado de este flujo no está en el repositorio. Esta es la descripción nodo por nodo para reconstruirlo; el que sí está exportado es el de Telegram (`n8n/telegram_rag_workflow.json`).

1. **Email Trigger (IMAP)** — se conecta a la cuenta de alertas, carpeta `INBOX`, con "marcar como leído" activado. Descargar el mensaje completo, no solo los encabezados.
2. **Code** — importa la lógica de `shared/parseGpsEmail.js` (el directorio se monta en el contenedor de n8n como `/files/shared`) y la aplica al mensaje:
   ```js
   const { parseGpsEmail } = require('/files/shared/parseGpsEmail.js');
   return [{ json: parseGpsEmail({
     subject: $json.subject,
     textPlain: $json.textPlain,
     textHtml: $json.textHtml,
     mailDate: $json.date,
     email_message_id: $json.messageId,
   }) }];
   ```
   > Ejecutar `require` de un archivo externo exige `NODE_FUNCTION_ALLOW_EXTERNAL` / `NODE_FUNCTION_ALLOW_BUILTIN` en el contenedor de n8n. La alternativa usada en producción fue pegar el contenido del parser dentro del nodo *Code*, a costa de tener que sincronizarlo a mano cuando cambia.
3. **IF** — descarta los eventos con `parse_ok === false` (sin unidad o sin hora) hacia una rama de revisión manual. Un evento sin unidad no es útil y contamina las vistas.
4. **Postgres** — inserción con `ON CONFLICT (source_hash) DO UPDATE`, con el mismo SQL que `upsertGpsEvent()` en `scripts/backfill_gps_event.js`.

## Deuda conocida

- **La persona del RAG no corresponde a este dominio.** `app/persona_config.py` define un asistente de reclutamiento (heredado del proyecto de Capital Humano) y `app/app.py` enruta por palabras clave de RH. La capa de recuperación funciona, pero para el uso operativo de flota hay que reemplazar el `SYSTEM_PROMPT` y el router.
- **El bot conversacional no consulta `gps_event`.** Hoy `/ask` responde sobre documentos PDF indexados. Las consultas de estatus de unidad se resolvieron con nodos de Postgres en n8n contra las vistas. Unificar ambos caminos —darle al LLM acceso a consultas SQL controladas— es el siguiente paso natural.
- **El workflow de ingesta no está versionado** (ver arriba).
