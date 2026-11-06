# Migraciones

Conserva aquí las migraciones ordenadas e inmutables una vez aplicadas. Cada cambio debe poder verificarse desde un esquema vacío y desde la versión previa soportada. Registra la estrategia de recuperación para cambios que transforman o eliminan datos.

Aplica [0001_ingestion_acceptance.sql](0001_ingestion_acceptance.sql) una vez contra la base dedicada a ingesta. Configura la conexión de `psql` mediante un servicio de PostgreSQL o las variables `PG*`. Ejecuta desde la raíz del proyecto.

```sh
psql -X -v ON_ERROR_STOP=1 -f infra/postgres/migrations/0001_ingestion_acceptance.sql
```

Continúa en orden con `0002_source_events.sql`, `0003_publication.sql` y `0004_private_acl.sql` usando el mismo comando. Una base con v1 aplicado comienza en `0002`. Cada archivo usa su propia transacción. La migración de eventos añade un registro separado y conserva los recibos históricos.
