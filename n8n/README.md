# Workflows de n8n

Importar desde `http://localhost:5678` → *Import from File*. Las credenciales viajan como referencias (`__RELINK__` o un id interno), **nunca con el token**: hay que reconectarlas a mano tras importar.

| Archivo | Qué hace | Estado |
|---|---|---|
| `imap_ingest_workflow.json` | Correo de alerta → parser → `gps_event` | Reconstruido y verificado; **generado**, no se edita a mano |
| `telegram_rag_workflow.json` | Telegram → comandos de flota o pregunta libre → API `/ask` → respuesta | Vigente |
| `kommo_crm_workflow.json` | Telegram → alta/búsqueda de contacto en Kommo CRM → API → respuesta | Histórico, requiere ajustes |

---

## `imap_ingest_workflow.json`

El corazón del sistema: convierte cada alerta del proveedor en una fila de `gps_event`.

```
Email Trigger (IMAP)          no leídos; al procesarlos los marca como leídos
  └─ Parse GPS Email          parser embebido (idéntico al del backfill)
      └─ IF parse_ok
          ├─ true  → Postgres: Upsert gps_event   ON CONFLICT (source_hash)
          └─ false → Sin unidad u hora → revisar
```

### No lo edites: regenéralo

```bash
node scripts/build_ingest_workflow.js
```

El nodo *Code* lleva `shared/parseGpsEmail.js` embebido, porque n8n no puede hacer `require` de un archivo del disco sin `NODE_FUNCTION_ALLOW_EXTERNAL`. Editar el JSON a mano crearía una segunda versión del parser que se separaría del original en silencio; el generador garantiza que sean el mismo código.

El generador también reemplaza `require('crypto')` por un SHA-256 en JavaScript puro, y **se niega a escribir el archivo** si ese hash no coincide con el de `crypto`. Si difiriera, el mismo correo entraría dos veces con `source_hash` distintos y se rompería la idempotencia de la que depende todo el sistema.

### Antes de importarlo

1. Reconecta las credenciales `ALERTAS_GPS_IMAP` (IMAP) y `GPS_POSTGRES` (Postgres); van como `__RELINK__`.
2. Revisa que las versiones de nodo (`emailReadImap` v2, `code` v2, `if` v2, `postgres` v2.4) existan en tu instancia. Es una reconstrucción, no el export original: en n8n más nuevo puede pedir ajustes.
3. Apunta el trigger a `INBOX`. La carpeta del backfill (`HISTORICAL`) debe quedar fuera: ambos usan la marca de "no leído" como trabajo pendiente y se robarían los correos.
4. La rama de revisión es un *No Operation*. Si quieres enterarte de los correos que el parser no entiende, sustitúyelo por una notificación a Telegram o un insert en una tabla de descartes.

---

## `telegram_rag_workflow.json`

El bot que usa Tráfico. Acepta comandos y también preguntas escritas con sus palabras.

```
Telegram Trigger
  └─ Set extract          chat_id, tipo de chat, texto
      └─ IF private       ignora los grupos donde se agregó al bot
          └─ Rutear comando          /estatus T-142 → "estatus de la unidad T-142"
              └─ IF necesita API
                  ├─ sí → API: /ask → Partir respuesta → Telegram: Send
                  └─ no → Respuesta directa ──────────→ Telegram: Send
```

### Comandos

| Comando | Responde |
|---|---|
| `/estatus <unidad>` | Dónde está y desde hace cuánto |
| `/disponibles` | Unidades en base o en la zona de La Laguna |
| `/sinsenal` | Sin señal o sin reportar |
| `/atoradas` | Detenidas en ruta por tiempo prolongado |
| `/combustible <unidad>` | Cargas y descargas de los últimos 7 días |
| `/eventos <unidad>` | Últimos eventos registrados |
| `/salud` | Si está entrando información al sistema |
| `/start`, `/ayuda` | Lista de comandos |

Escribir `/estatus T-142` es más rápido y más predecible que redactar la pregunta: no depende de que el router de intenciones reconozca la frase. Quien prefiera preguntar normal, puede — el mensaje pasa tal cual a `/ask`.

### Por qué el nodo de comandos no consulta la base

Traduce el comando a la pregunta equivalente y deja que `/ask` haga el trabajo. Podría llamar directo a `/fleet/status`, pero ese endpoint devuelve JSON y habría que darle formato aquí, creando una segunda versión del formato que se separaría de la de la API. Así el texto —unidad, hora, aviso de dato viejo— sale de un solo lugar.

### Detalles que importan

- **`IF private`** evita que el bot conteste en los grupos donde fue agregado, que era la fuente principal de ruido.
- **`Partir respuesta`** existe porque Telegram corta los mensajes en 4096 caracteres y una lista de flota completa los pasa con facilidad. La API ya devuelve `chunks` partidos por salto de línea; el nodo los convierte en items para que se envíen como mensajes separados, en orden.
- **`/estatus@nombre_del_bot`** funciona: en grupos, Telegram agrega el sufijo y el nodo lo limpia.

### Después de importarlo

Registra los comandos en BotFather (`/setcommands`) para que aparezcan en el menú del chat:

```
estatus - Dónde está una unidad
disponibles - Unidades libres
sinsenal - Unidades sin señal
atoradas - Detenidas en ruta
combustible - Cargas y descargas de una unidad
eventos - Últimos eventos de una unidad
salud - Si está entrando información
ayuda - Lista de comandos
```

Los comandos de flota necesitan `DATABASE_URL` en el servicio `api`. Sin eso, `/ask` responde solo sobre documentos y las consultas de estatus terminan en "no tengo ese dato" — ver [`../docs/consultas-del-bot.md`](../docs/consultas-del-bot.md).

## `kommo_crm_workflow.json`

Añade identidad al bot: antes de responder, resuelve quién está preguntando contra el CRM.

```
Telegram Trigger
   └─ Normalize Telegram Data      (aplana user_id, chat_id, nombre, texto)
       └─ Kommo: Get list of contacts   (busca por telegram_user_id)
           └─ Normalize Kommo Response  (Kommo devuelve el string "empty"
                                         cuando no hay resultados; aquí se
                                         convierte en una lista vacía)
               └─ IF contacts.length > 0
                   ├─ sí → Use existing contact_id
                   └─ no → Kommo: Create new contacts → Use new contact_id
                       └─ HTTP Request → API
                           └─ Telegram: Send
```

Dos detalles que valen al reutilizarlo:

- **`Normalize Kommo Response` no es adorno.** La API de Kommo responde con el texto plano `"empty"` en vez de una lista vacía cuando no hay coincidencias. Sin ese nodo, el `IF` de abajo revienta.
- **`field_id: 3753238`** es el campo personalizado de Kommo donde se guarda el `telegram_user_id`. Ese id es de la cuenta original: **cámbialo por el tuyo** o el alta de contactos fallará en silencio.

### Antes de usarlo, hay que actualizarlo

Quedó de una versión anterior de la API y no funciona tal cual contra la actual:

| En el workflow | Hoy |
|---|---|
| `POST http://rag_api:8000/query` | El servicio se llama `api` y el endpoint es `/ask` → `http://api:8000/ask` |
| Lee `{{$json["answer"]}}` | La respuesta trae `text` (y `chunks` ya partidos para Telegram) → `{{$json["text"]}}` |

## Sobre el original de la ingesta

El export original **no se conserva**: n8n guardaba sus workflows en la base `n8ndb`, dentro del volumen de Postgres del proyecto, y ese volumen se eliminó al migrar el servidor. `imap_ingest_workflow.json` es una reconstrucción hecha a partir del parser y del SQL del backfill, que sí sobrevivieron.

> Exporta tus workflows al repositorio. Un workflow que solo existe dentro de n8n se pierde con el volumen.
