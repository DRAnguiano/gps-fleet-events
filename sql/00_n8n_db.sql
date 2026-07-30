-- sql/00_n8n_db.sql
--
-- n8n guarda sus workflows y credenciales en Postgres (DB_TYPE=postgresdb en
-- docker-compose.yml), en una base aparte de la operativa. La imagen de
-- Postgres solo crea la base de POSTGRES_DB, así que hay que crear esta a
-- mano o n8n arranca en bucle de reintento con
-- "database n8ndb does not exist".
--
-- Se ejecuta antes que el esquema operativo por el prefijo 00.

SELECT 'CREATE DATABASE n8ndb OWNER gps'
WHERE NOT EXISTS (
  SELECT FROM pg_database WHERE datname = 'n8ndb'
)\gexec
