# Reglas de negocio operativas

Implementación: [`sql/02_vistas_operativas.sql`](../sql/02_vistas_operativas.sql).

Este documento explica **por qué** cada regla es como es. Los umbrales vienen de la operación real de una flota en la Comarca Lagunera; si los reutilizas en otra empresa, revísalos uno por uno.

## La pregunta que hay que responder

Tráfico necesita asignar el siguiente viaje. La pregunta no es "¿dónde está la unidad?" sino **"¿puedo contar con esta unidad ahora?"**. El correo del proveedor no responde eso: dice que la unidad reportó velocidad 0 cerca de tal lugar. Traducir lo segundo en lo primero es todo el valor del sistema.

## Regla 1 — Movimiento real: 5 km/h

Una unidad se considera en movimiento con `speed_kmh >= 5`.

El umbral no es cero porque el GPS reporta velocidades de 1 a 3 km/h con la unidad parada, por deriva de la señal. Contarlas como movimiento haría que una unidad estacionada toda la noche apareciera como "en ruta".

`v_unidad_ultimo_movimiento` guarda el último evento que superó ese umbral. De ahí sale `horas_sin_moverse`, que es el número que Tráfico realmente mira.

## Regla 2 — Tres paradas distintas que se ven iguales

Con velocidad 0, el correo no distingue entre situaciones que operativamente son opuestas:

| Estatus | Significado | Qué implica para Tráfico |
|---|---|---|
| `EN_BASE` | En patio o base | **Disponible.** Se le puede asignar viaje ya |
| `EN_ZONA_LAGUNA` | Detenida en el área metropolitana de La Laguna | Muy probablemente el operador se la llevó a su casa. Disponible, pero hay que llamarle |
| `PARADA_EN_RUTA` | Detenida fuera de zona conocida | **No disponible.** Atorada, en caseta, descansando o con incidente |

La distinción `EN_ZONA_LAGUNA` existe por una particularidad de esta empresa: **las unidades se quedan con el operador**, no regresan al patio al terminar el viaje. Sin esta regla, decenas de unidades disponibles aparecerían como "paradas en ruta" y Tráfico las descartaría.

La zona se detecta por texto de ubicación: `torreon`, `gomez palacio`, `lerdo`, `matamoros`, `la laguna` — los municipios del área metropolitana. Es una heurística sobre texto libre; con coordenadas y geocercas propias sería exacta.

`PARADA_EN_RUTA` incluye deliberadamente el "atoramiento en carretera": una unidad detenida por tráfico, revisión o falla en un tramo carretero. Para la decisión de asignación no importa cuál de las tres sea — en ninguna se puede contar con la unidad.

## Regla 3 — Detención larga: 8 horas

`detencion_larga` marca las unidades sin moverse por más de 8 horas.

Ocho horas es aproximadamente un descanso completo. Cruzado con el estatus, el significado cambia por completo:

- `EN_BASE` + detención larga → unidad ociosa; se le puede sacar provecho.
- `EN_ZONA_LAGUNA` + detención larga → el operador está en su casa; disponible con aviso previo.
- `PARADA_EN_RUTA` + detención larga → **anomalía.** Nadie descansa 8 horas a mitad de un tramo sin motivo. Amerita llamada.

## Regla 4 — Qué hace la unidad y hace cuánto lo sabemos son dos preguntas

`v_unidad_estatus` las responde por separado:

- **`estatus`** — qué está haciendo, según su último reporte.
- **`lectura_reciente`** (y `horas_sin_reporte`) — si ese dato es de fiar. La ventana es de 60 minutos.

Mezclarlas fue un error de la primera versión de la vista, y vale la pena entender por qué. `SIN_LECTURA_RECIENTE` era un valor de `estatus`, así que **pisaba la clasificación**: una unidad esperando en patio se veía igual que una atorada en carretera, porque ninguna de las dos genera alertas mientras está detenida. Se perdía justo la distinción que sirve para decidir. Hoy esa unidad reporta `EN_BASE` con `lectura_reciente = false`, y el bot lo dice tal cual: *"en base o patio (disponible) … ⚠ último reporte hace 10 h"*.

`SIN_SENAL` sí es un estatus, y es distinto: no es que falten reportes, es que el proveedor **confirmó** la pérdida de comunicación con un evento `CONNECTION_LOST`.

La falta de lectura reciente sigue sirviendo para **detectar fallas del sistema**: si de pronto muchas unidades la pierden a la vez, el problema no está en la flota sino en el buzón, en n8n o en la base. Por eso el bot responde `¿está entrando información?` con la última carga registrada.

La ventana de 60 minutos depende de qué tan seguido dispare alertas tu configuración del proveedor. Con alertas esporádicas hay que ampliarla, o casi toda la flota aparecerá sin lectura reciente.

## Regla 5 — Combustible

`v_combustible_diario` agrega por unidad y día los litros cargados (`FUEL_FILL`), los descargados (`FUEL_DRAIN`) y el rango de odómetro. Es la base del tablero de Power BI y de la detección de anomalías.

Dos precauciones:

- **Las descargas no son necesariamente robo.** Un `FUEL_DRAIN` puede ser trasvase legítimo, sensor descalibrado o movimiento del combustible en el tanque al subir una pendiente. La señal útil es el patrón —misma unidad, mismo tramo, misma hora, de forma repetida— no el evento aislado.
- **El día se corta en hora local** (`America/Monterrey`). Un evento de las 23:40 pertenece al día operativo correcto y no al siguiente en UTC.

El scoring de anomalías se dejó pendiente a propósito: primero hacía falta un histórico suficiente para saber qué es normal en cada unidad.

## Ajustar los umbrales

Están escritos directamente en las vistas, comentados en el encabezado del archivo:

```
Umbral de movimiento .......... 5 km/h
Ventana de "reciente" ......... 60 min
Detención larga ............... 8 h
```

Para cambiarlos, edita `sql/02_vistas_operativas.sql` y vuelve a aplicarlo. El archivo elimina y recrea las vistas, así que se puede reaplicar sobre una base en uso sin tocar los datos —pero hay que volver a conceder los permisos del usuario de Power BI:

```bash
docker exec -i gps_postgres psql -U gps -d gpsdb < sql/02_vistas_operativas.sql
```

Cambiar un umbral cambia lo que ve Power BI y lo que responde el bot, en el mismo momento y de forma consistente. Ese es exactamente el motivo de que las reglas vivan en SQL y no repartidas entre la API y el tablero.
