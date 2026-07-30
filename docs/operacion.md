# Operación: backfill, respaldos y recuperación

El servidor vive en las oficinas de la empresa, sin UPS. Un corte de energía —que ahí ocurre— puede apagar Postgres a media escritura y dejar la ingesta con un hueco. La estrategia de continuidad tiene dos capas, en este orden:

1. **Respaldo diario de la base** — recuperación rápida.
2. **El buzón de Gmail como fuente de verdad** — recuperación total, aunque se pierda todo lo demás.

La segunda capa es la que de verdad da tranquilidad: mientras los correos sigan en Gmail, la base entera se puede reconstruir.

---

## Respaldo diario

`scripts/backup_db.sh` genera un dump comprimido, verifica que no esté truncado y rota los antiguos.

```bash
./scripts/backup_db.sh
```

| Variable | Default | Qué controla |
|---|---|---|
| `BACKUP_DIR` | `./backups` | Dónde se guardan |
| `RETENTION_DAYS` | `14` | Días antes de borrar |
| `PG_CONTAINER` | `gps_postgres` | Contenedor de Postgres |

Programado con cron a las 03:00:

```cron
0 3 * * * cd /ruta/al/proyecto && ./scripts/backup_db.sh >> backups/backup.log 2>&1
```

El script verifica el gzip antes de rotar. Un dump interrumpido por un corte de energía es peor que no tener dump: parece bueno y desplaza a los respaldos válidos. Si la verificación falla, el archivo se borra y **no** se rota nada.

`backups/` está en `.gitignore`. En serio: copia esos archivos a otra máquina o a la nube. Un respaldo que vive en el mismo disco que la base no protege contra la falla más probable.

**Restaurar:**

```bash
gunzip -c backups/gpsdb_20260314_030001.sql.gz \
  | docker exec -i gps_postgres psql -U gps -d gpsdb
```

El dump se genera con `--clean --if-exists`, así que se puede restaurar sobre una base existente sin borrarla antes.

---

## Backfill / recuperación desde correo

`scripts/backfill_gps_event.js` se conecta por IMAP, toma los correos **no leídos** de una carpeta, los parsea con el mismo módulo del flujo en vivo y hace upsert en `gps_event`. Al terminar marca los mensajes como leídos —los procesados con éxito y los rechazados por igual— para no reprocesar eternamente un correo que el parser no entiende.

Sirve para dos cosas:

- **Carga inicial:** meter a la base todo el histórico de alertas que ya estaba en el buzón antes de que existiera el sistema.
- **Recuperación:** rellenar el hueco que dejó un corte de energía o una caída de n8n.

### Corrida por lotes

`run_backfill.sh` envuelve al script y lo corre repetidamente en bloques, con pausas, para no toparse con los límites de Gmail:

```bash
./run_backfill.sh
```

| Variable | Default | Qué controla |
|---|---|---|
| `MAX_TOTAL` | `6882` | Tope de correos de toda la corrida |
| `BLOCK` | `100` | Correos por bloque antes de la pausa |
| `COOLDOWN` | `60` | Segundos de pausa entre bloques |
| `BATCH_SIZE` (en `.env`) | `20` | Correos por invocación del script |
| `DOCKER_NETWORK` | `bot_gps_operativo_default` | Red donde vive Postgres |

Verifica el nombre real de la red antes de la primera corrida —depende del nombre de la carpeta del proyecto:

```bash
docker network ls | grep postgres || docker network ls
```

Un candado en `/tmp/backfill_gps.lock` impide dos corridas simultáneas.

### Códigos de salida del script de Node

| Código | Significado |
|---|---|
| `0` | Se procesaron eventos válidos; hay que seguir |
| `2` | No quedan correos sin leer, o el lote no produjo filas válidas → detener |
| `1` | Error real (IMAP, base de datos) |

`run_backfill.sh` los usa para decidir si continuar o parar. El script imprime `FINAL_SUMMARY={...}` con `okCount`, `rejectCount`, `fetchedCount` y `seenMarkedCount`, que es lo que el wrapper acumula para reportar progreso.

### Por qué es seguro correrlo de más

Todas las inserciones son `ON CONFLICT (source_hash) DO UPDATE`. Reprocesar un correo ya ingerido actualiza la fila en lugar de duplicarla. Se puede interrumpir a media corrida, volver a lanzar, o pasar el buzón completo otra vez sin ensuciar los datos.

### Separar histórico de tiempo real

El flujo en vivo de n8n lee `INBOX`. El backfill lee la carpeta indicada en `IMAP_MAILBOX` (`HISTORICAL` por defecto). Se separan porque ambos usan la bandera "no leído" como marca de trabajo pendiente: si compartieran carpeta, se robarían los correos entre sí.

Para recuperar un hueco, mueve en Gmail los correos del rango afectado a la carpeta del backfill, **márcalos como no leídos** y lanza el script.

---

## Procedimiento tras un corte de energía

1. **Levantar servicios y verificar:**
   ```bash
   docker compose up -d
   docker compose ps
   curl http://localhost:8000/health
   ```

2. **Medir el hueco** — hasta dónde llegó la ingesta:
   ```sql
   SELECT MAX(event_time) AS ultimo_evento,
          MAX(created_at)  AS ultima_carga
   FROM gps_event;
   ```

3. **Comprobar que la base está sana.** Si Postgres no arranca o reporta corrupción, restaura el último respaldo antes de seguir.

4. **Rellenar desde el correo.** En Gmail, mueve a la carpeta del backfill los correos posteriores al último `event_time`, márcalos como no leídos y corre:
   ```bash
   ./run_backfill.sh
   ```

5. **Verificar continuidad** — que no queden horas sin eventos:
   ```sql
   SELECT date_trunc('hour', event_time) AS hora, COUNT(*)
   FROM gps_event
   WHERE event_time >= now() - INTERVAL '3 days'
   GROUP BY hora
   ORDER BY hora;
   ```

6. **Revisar el bot.** Si ngrok cambió de dominio, hay que reconfigurar el webhook de Telegram en n8n.

---

## Vigilancia rutinaria

**¿Sigue entrando información?**

```sql
SELECT MAX(created_at) FROM gps_event;
```

Sin filas nuevas en horario operativo: revisar n8n, la conexión IMAP y la contraseña de aplicación de Gmail (caduca si se cambia la contraseña de la cuenta).

**¿El parser está quedándose corto?**

```sql
SELECT type, COUNT(*)
FROM gps_event
WHERE created_at >= now() - INTERVAL '7 days'
GROUP BY type
ORDER BY 2 DESC;
```

Un `OTHER` que crece indica que el proveedor cambió el formato o agregó un tipo de alerta. Ver [parser-correo.md](parser-correo.md#extender-el-parser).

**Logs:**

```bash
docker compose logs -f n8n
docker compose logs -f api
docker compose logs --tail=100 postgres
```
