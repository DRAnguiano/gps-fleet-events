# GPS Fleet Events

**Telemetría operativa de flota reconstruida a partir de correos de alerta, cuando el proveedor de monitoreo no ofrece API.**

Una empresa de transportes mediana no tenía forma programática de consultar el estado de sus unidades: el proveedor de GPS solo entregaba una interfaz web y **alertas por correo electrónico**. Toda pregunta operativa —"¿esta unidad ya está libre?", "¿cuánto lleva parada?", "¿ya cargó combustible?"— pasaba por Monitoreo, por teléfono o por radio, y Tráfico esperaba la respuesta antes de poder asignar el siguiente viaje.

Este proyecto convierte ese buzón de correo en una base de datos consultable, y esa base en respuestas inmediatas: un tablero de Power BI y un bot de Telegram que responde en lenguaje natural.

> **El resultado operativo:** Tráfico dejó de preguntar. La decisión de qué operador sale al siguiente viaje se toma con datos, en el momento.

---

## El problema, en concreto

| Restricción | Consecuencia |
|---|---|
| Sin API del proveedor de monitoreo | No hay forma de consultar estado de unidades por programa |
| Solo alertas por correo | La información existe, pero dispersa en miles de correos sin estructura |
| Consulta a Monitoreo por teléfono | Latencia de minutos u horas en cada decisión de asignación |
| Las unidades se quedan con el operador | "Detenida" puede significar disponible, en casa del operador, o atorada en carretera |
| Servidor local sin UPS | Un corte de energía puede dejar la base incompleta |

## La solución

```
Proveedor GPS ──(alerta por correo)──> Gmail/IMAP
                                          │
                                          ▼
                                    n8n (trigger IMAP)
                                          │
                              shared/parseGpsEmail.js
                          (regex + normalización + hash)
                                          │
                                          ▼
                                  PostgreSQL · gps_event
                                          │
                        ┌─────────────────┼─────────────────┐
                        ▼                 ▼                 ▼
                 vistas SQL          Power BI          FastAPI + RAG
              (reglas de negocio)   (combustible,     (Ollama local)
                        │            tiempo en ruta)         │
                        └──────────> Bot de Telegram <───────┘
                                          │
                                    Tráfico decide
```

Cada pieza y su porqué está en [`docs/arquitectura.md`](docs/arquitectura.md).

---

## Qué resuelve cada parte

### 1. Ingesta: el correo como API
El proveedor manda una alerta por cada evento (llenado de combustible, descarga, exceso de velocidad, cruce de caseta, pérdida de conexión). `shared/parseGpsEmail.js` extrae de cada correo la unidad, la hora, el tipo de evento, el lugar, la velocidad y los litros, y calcula un **SHA-256 del contenido normalizado** que actúa como llave de idempotencia: el mismo correo procesado dos veces actualiza la fila, nunca la duplica.

El parser es el mismo módulo en los dos caminos —el flujo en vivo de n8n y el backfill histórico— para que ambos produzcan exactamente la misma fila. Detalle del catálogo de eventos y los patrones reconocidos: [`docs/parser-correo.md`](docs/parser-correo.md).

### 2. Modelo de datos: una tabla, indexada por la pregunta real
Todo vive en `gps_event`, con las columnas estables como campos y lo variable en un `JSONB`. El índice principal es `(unit_code, event_time DESC)`, porque la consulta dominante es siempre "el último estado de esta unidad". Esquema comentado: [`docs/base-de-datos.md`](docs/base-de-datos.md).

### 3. Reglas de negocio: distinguir tres paradas que se ven iguales
Una unidad con velocidad 0 puede estar disponible en patio, con el operador en su casa, o atorada en carretera. En el correo las tres se ven idénticas. Las vistas de [`sql/02_vistas_operativas.sql`](sql/02_vistas_operativas.sql) las separan cruzando velocidad, geocerca, zona geográfica (área metropolitana de La Laguna) y tiempo transcurrido. El razonamiento detrás de cada umbral: [`docs/reglas-negocio.md`](docs/reglas-negocio.md).

### 4. Consulta: Power BI y bot conversacional
Power BI se conecta a las vistas para combustible y tiempo en carretera. En paralelo, un bot de Telegram permite preguntar en lenguaje natural: n8n recibe el mensaje y llama a `/ask`, que resuelve por dos caminos según la pregunta:

- **Consulta operativa** ("¿dónde está la T-142?", "¿qué unidades están libres?") → consultas SQL fijas contra `gps_event` y sus vistas. El LLM no participa: los datos vienen exactos de la base y se formatean con plantillas, siempre con unidad, hora y advertencia si el dato es viejo.
- **Consulta documental** ("¿qué dice el manual sobre…") → RAG con un LLM local (Ollama) sobre los PDF indexados.

El modelo **nunca genera SQL**. El porqué y el detalle de las siete intenciones reconocidas están en [`docs/consultas-del-bot.md`](docs/consultas-del-bot.md).

### 5. Continuidad: el buzón es el respaldo
El servidor está en sitio y sin UPS. La recuperación tiene dos capas:

- **Respaldo diario** de Postgres con rotación y verificación de integridad (`scripts/backup_db.sh`).
- **Reconstrucción desde origen:** los correos siguen en Gmail. `scripts/backfill_gps_event.js` los vuelve a bajar, parsear e insertar. Gracias al `source_hash`, reprocesar todo el buzón es seguro. Procedimiento completo: [`docs/operacion.md`](docs/operacion.md).

---

## Stack

| Componente | Rol |
|---|---|
| PostgreSQL 16 | Almacén de eventos y vistas de negocio |
| n8n | Orquestación: IMAP → parser → BD, y Telegram → API |
| Node.js | Parser de correo y backfill histórico (`imapflow`, `mailparser`, `pg`) |
| FastAPI | API de consulta (`/ask`, `/search`, `/reindex`) |
| Ollama | LLM local (sin enviar datos de la empresa a terceros) |
| ChromaDB + LlamaIndex | Índice vectorial para RAG sobre documentos internos |
| Power BI | Tablero de combustible y tiempo en ruta |
| Docker Compose | Despliegue completo en el servidor local |
| ngrok | Expone n8n para los webhooks de Telegram |

---

## Puesta en marcha

**Requisitos:** Docker, Docker Compose y, opcionalmente, GPU NVIDIA con `nvidia-container-toolkit`.

```bash
git clone https://github.com/DRAnguiano/gps-fleet-events.git
cd gps-fleet-events

cp .env.example .env
# Edita .env: credenciales de Postgres, IMAP (contraseña de aplicación de
# Gmail), token de Telegram y dominio de ngrok.

docker compose up -d --build
```

Postgres ejecuta `sql/01_schema.sql` y `sql/02_vistas_operativas.sql` automáticamente la primera vez que se crea el volumen.

Verificar:

```bash
docker compose ps
curl http://localhost:8000/health          # {"status":"ok"}
```

Indexar los documentos de `data/` y hacer una consulta:

```bash
curl -X POST http://localhost:8000/reindex
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"q":"¿Cuál es el procedimiento cuando una unidad pierde señal en ruta?","top_k":4}'
```

Servicios expuestos: API `:8000` · n8n `:5678` · Adminer `:8080` · Ollama `:11434`.

> `data/` no se versiona: contiene documentos internos de la empresa. Coloca ahí tus propios PDF antes de reindexar.

### Cargar los workflows de n8n

Abre `http://localhost:5678` e importa los workflows de `n8n/`, empezando por `imap_ingest_workflow.json`, que es el que alimenta la base. Las credenciales viajan como referencias y hay que reconectarlas desde la interfaz.

El workflow de ingesta **se genera, no se edita**: lleva el parser embebido y `node scripts/build_ingest_workflow.js` lo mantiene sincronizado con `shared/parseGpsEmail.js`. El porqué está en [`n8n/README.md`](n8n/README.md) y [`docs/arquitectura.md`](docs/arquitectura.md#flujo-de-ingesta-en-n8n).

### Backfill histórico

```bash
./run_backfill.sh
```

Procesa el buzón por lotes con pausas entre bloques para no toparse con los límites de Gmail. Es idempotente: se puede interrumpir y reanudar. Ver [`docs/operacion.md`](docs/operacion.md).

---

## Estructura

```
app/
  app.py                  API FastAPI (/ask, /fleet/status, /search, /reindex)
  fleet_intent.py         Router de consultas operativas y formato de respuesta
  fleet_queries.py        Consultas SQL fijas sobre gps_event y sus vistas
  indexer.py              Capa RAG (índice vectorial, LLM)
  persona_config.py       Persona del asistente operativo
shared/parseGpsEmail.js   Parser de alertas — compartido por n8n y el backfill
scripts/
  backfill_gps_event.js   Reconstrucción histórica desde IMAP
  backup_db.sh            Respaldo diario con rotación
  build_ingest_workflow.js  Genera el workflow de ingesta desde el parser
sql/                      Esquema y vistas de negocio (init automático)
n8n/                      Workflows exportados (Telegram, CRM Kommo)
docs/                     Documentación técnica y de negocio
data/                     Documentos para RAG (no versionado)
```

## Documentación

- [Arquitectura y decisiones de diseño](docs/arquitectura.md)
- [Parser de correo y catálogo de eventos](docs/parser-correo.md)
- [Modelo de datos](docs/base-de-datos.md)
- [Reglas de negocio operativas](docs/reglas-negocio.md)
- [Consultas operativas del bot](docs/consultas-del-bot.md)
- [Operación: backfill, respaldos y recuperación](docs/operacion.md)

## Seguridad

- `.env` nunca se versiona; usa `.env.example` como plantilla.
- Para Gmail, usa una **contraseña de aplicación**, no la contraseña de la cuenta.
- `REINDEX_API_KEY` protege `/reindex`; `INCLUDE_ERROR_DETAILS` debe quedar en `false` en producción.
- Los correos originales se guardan en `raw_body`. Si tus alertas incluyen datos personales del operador, restringe el acceso a la base en consecuencia.

## Estado y siguientes pasos

Sistema en operación real. Pendientes:

- [ ] Scoring de anomalías de combustible (la base de eventos ya lo soporta)
- [ ] Geocercas propias en vez de inferirlas del texto de la alerta
- [ ] Probar el workflow de ingesta reconstruido contra una instancia de n8n en vivo (el original se perdió con los volúmenes del servidor)
- [ ] Detección de patrones por unidad y operador con modelos de ML

## Autor

**David Ramos** — Ingeniería en Datos e IA

## Licencia

MIT — ver [LICENSE](LICENSE).
