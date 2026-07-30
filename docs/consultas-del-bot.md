# Consultas operativas del bot

Cómo `/ask` responde preguntas de flota con la base de eventos en vez de con los documentos.

Implementación: [`app/fleet_queries.py`](../app/fleet_queries.py) (acceso a datos) y [`app/fleet_intent.py`](../app/fleet_intent.py) (router y formato).

## La decisión de fondo: el LLM no escribe SQL

Lo habitual sería darle al modelo el esquema y dejar que genere las consultas. Aquí no, por tres razones:

1. **Seguridad.** Un LLM con SQL libre contra la base de producción puede filtrar tablas completas o tumbar el servidor con un join accidental.
2. **Corrección.** Una consulta mal formada no falla: devuelve un número plausible pero equivocado. Con una decisión de asignación de viaje de por medio, eso es peor que no responder.
3. **Consistencia.** Las reglas de negocio ya viven en las vistas. Si el modelo improvisara su propio criterio de "disponible", el bot y Power BI dirían cosas distintas.

Entonces: consultas fijas y parametrizadas, y el modelo no participa en el camino operativo. Sigue redactando en las consultas documentales, que es donde aporta.

## Cómo funciona una consulta

```
"¿dónde está la T-142?"
        │
        ├─ detect_intent()  → STATUS          (reglas, no LLM)
        ├─ extract_unit()   → "t142"
        ├─ resolve_unit()   → "T-142"         (contra la base, tolerante a formato)
        ├─ unit_status()    → SELECT ... FROM v_unidad_estatus WHERE unit_code = %s
        └─ plantilla        → texto final
```

Si `detect_intent()` no reconoce nada, `/ask` sigue de largo al RAG documental. La capa operativa nunca secuestra una pregunta que no es suya.

**Y hay una segunda salida, que costó una prueba en vivo descubrir.** Las intenciones que necesitan una unidad (`STATUS`, `FUEL`, `EVENTS`) se disparan por una sola palabra, así que *"¿qué incluye el bono de rendimiento de combustible?"* caía en `FUEL` y el bot pedía un número de unidad en vez de buscar en los documentos. Ahora, cuando falta la unidad, se exige además algún marcador de flota —"unidad", "operador", "viaje", "tracto", "caseta"…— y si no aparece, la consulta se devuelve al RAG.

### La intención se detecta con reglas

Son siete formas de preguntar sobre una flota, no un problema de comprensión abierto. Unas reglas explícitas fallan de manera predecible y se arreglan agregando una palabra a una lista; un clasificador con LLM agrega latencia y falla de formas que nadie puede reproducir.

| Intención | Se dispara con | Responde con |
|---|---|---|
| `STATUS` | "dónde está", "estatus", "cómo está", "sigue parada" + unidad | `v_unidad_estatus` |
| `AVAILABLE` | "disponibles", "libres", "quién puede salir", "en patio" | `v_unidad_estatus` filtrada |
| `NO_SIGNAL` | "sin señal", "no reporta", "perdió conexión" | `v_unidad_estatus` |
| `LONG_STOPS` | "atoradas", "mucho tiempo parada", "paradas en ruta" | `v_unidad_estatus` |
| `FUEL` | "combustible", "litros", "cargó", "descarga" + unidad | `v_combustible_diario` |
| `EVENTS` | "últimos eventos", "qué ha hecho", "historial" + unidad | `gps_event` |
| `HEALTH` | "¿está entrando información?", "última señal" | `gps_event` (agregado) |

El orden de evaluación importa y está fijado a propósito: "¿qué unidades están sin señal?" menciona unidades **y** señal, y sin ese orden caería en disponibilidad.

### La unidad se resuelve contra la base

Tráfico escribe `T-142`, `t142`, `T 142` o "la 142". `resolve_unit()` normaliza —quita todo lo que no sea letra o número— y compara contra los `unit_code` que existen de verdad:

```sql
WHERE regexp_replace(upper(unit_code), '[^A-Z0-9]', '', 'g') = %s
```

Contra la base y no contra una lista fija, porque el catálogo de unidades cambia y el proveedor las nombra de formas distintas (`T-142`, `V MOVIL 4`). Si la unidad no existe, el bot lo dice en vez de responder sobre otra.

### La respuesta se arma con plantillas

Los datos ya vienen exactos de la base. Pasarlos por el LLM solo agregaría la posibilidad de que cambie una hora o invente una ubicación — precisamente lo que el `SYSTEM_PROMPT` le prohíbe. Las plantillas también garantizan lo que el prompt pide y un modelo pequeño olvida:

- **Unidad y hora siempre.** Un estatus sin hora no sirve para decidir.
- **Advertencia de dato viejo**, calculada con el timestamp en la mano en lugar de confiar en que el modelo lo note:

```
T-142: en base o patio (disponible).
Último reporte: 30/07 06:13 cerca de PATIO TORREON.
Sin moverse desde hace 11.1 h.
⚠ Último reporte hace 10.1 h; el dato puede no estar actualizado.
```

## Ejemplos reales

```
>>> ¿qué unidades están disponibles?
Unidades disponibles:
  T-142 — en base o patio (disponible), 11.1 h sin moverse (PATIO TORREON). …
  T-500 — detenida en la zona de La Laguna (probablemente con el operador en
          su domicilio), 9.1 h sin moverse (LERDO, DGO.). …

>>> ¿hay unidades atoradas en ruta?
Unidades detenidas en ruta por tiempo prolongado:
  T-311 — detenida en ruta, 15.1 h sin moverse (CARR. DURANGO KM 12). …

>>> ¿cuánto combustible cargó la T-142?
T-142 — combustible, últimos 7 días:
  29/07: descargó 85.5 l (1 evento(s)).
  28/07: cargó 300 l (1 evento(s)).

>>> ¿dónde está la T-999?
No encuentro la unidad T-999 en los eventos registrados. Verifica el número.
```

## Desde Telegram

El workflow `n8n/telegram_rag_workflow.json` expone estas consultas como comandos (`/estatus T-142`, `/disponibles`, `/sinsenal`, `/atoradas`, `/combustible`, `/eventos`, `/salud`). El nodo de comandos no consulta la base: traduce el comando a la pregunta equivalente y la manda a `/ask`, para que el formato del texto salga de un solo lugar. Detalle en [`../n8n/README.md`](../n8n/README.md).

## Endpoint directo

Para n8n o tableros, sin pasar por la detección de intención:

```bash
curl "http://localhost:8000/fleet/status?unit=T-142"
curl "http://localhost:8000/fleet/status"   # disponibles, sin señal y detenciones largas
```

Responde `404` si la unidad no existe y `503` si la base operativa no está configurada.

## Configuración

| Variable | Default | Para qué |
|---|---|---|
| `DATABASE_URL` | *(vacío)* | Conexión a `gpsdb`. **Vacío desactiva toda la capa** y `/ask` solo responde sobre documentos |
| `FLEET_TZ` | `America/Monterrey` | Zona en que se muestran las horas |
| `FLEET_QUERY_TIMEOUT_S` | `5` | Corta consultas colgadas: el bot responde por Telegram y nadie espera |
| `FLEET_STALE_HOURS` | `1` | A partir de cuántas horas sin reportar se advierte que el dato es viejo |
| `RAG_MIN_SCORE` | `0.35` | Similitud mínima para contestar con documentos. Depende del modelo de embeddings: con `all-MiniLM-L6-v2` un acierto claro ronda 0.40-0.50 y el ruido se queda en 0.20. Compruébalo con `POST /search`, que devuelve los scores |

En `docker-compose.yml`, el servicio `api` ya recibe `DATABASE_URL`.

## Degradación

La capa operativa es un extra, no un requisito:

- Sin `DATABASE_URL`, `try_answer()` devuelve `None` y todo sigue por el RAG documental.
- Si la base se cae a medio día, la excepción se registra y la consulta cae también al RAG. El bot responde peor, pero responde.
- El pool se abre de forma perezosa: la API arranca aunque Postgres todavía no esté listo.

## Extender

**Para agregar una consulta:** una función en `fleet_queries.py` con SQL fijo y parámetros (nunca interpolando texto), una entrada en `_INTENTS` y una plantilla en `fleet_intent.py`.

**Si la pregunta necesita una regla de negocio nueva** —"¿qué unidades llevan más de X días sin viaje?"— la regla va en una vista de `sql/02_vistas_operativas.sql`, no en Python. Así el bot, Power BI y las consultas manuales siguen respondiendo lo mismo.
