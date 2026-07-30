# Workflows de n8n

Importar desde `http://localhost:5678` → *Import from File*. Las credenciales viajan como referencias (`__RELINK__` o un id interno), **nunca con el token**: hay que reconectarlas a mano tras importar.

| Archivo | Qué hace | Estado |
|---|---|---|
| `telegram_rag_workflow.json` | Telegram → API `/ask` → respuesta | Vigente |
| `kommo_crm_workflow.json` | Telegram → alta/búsqueda de contacto en Kommo CRM → API → respuesta | Histórico, requiere ajustes |
| *(ingesta IMAP → Postgres)* | Correo de alerta → parser → `gps_event` | **Perdido**, ver abajo |

---

## `telegram_rag_workflow.json`

El camino corto: mensaje de Telegram, filtro de chats privados, `POST http://api:8000/ask`, respuesta al chat.

El filtro `IF private` está a propósito: evita que el bot conteste en grupos donde fue agregado, que era la fuente principal de ruido.

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

## Ingesta IMAP → Postgres

**No se conserva.** n8n guardaba sus workflows en la base `n8ndb`, dentro del volumen de Postgres del proyecto, y ese volumen se eliminó al migrar el servidor. Nunca se exportó a archivo.

La descripción nodo por nodo para reconstruirlo está en [`../docs/arquitectura.md`](../docs/arquitectura.md#flujo-de-ingesta-en-n8n). La lógica difícil —el parseo de las alertas— no se perdió: vive en [`../shared/parseGpsEmail.js`](../shared/parseGpsEmail.js).

> Exporta tus workflows al repositorio. Un workflow que solo existe dentro de n8n se pierde con el volumen.
