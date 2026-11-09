# PostgreSQL migrations

Apply immutable files in numeric order. Verify an empty schema and upgrade from the supported predecessor. Record recovery steps for changes that transform or remove data.

Set the database connection with a PostgreSQL service or `PG*` variables. Run from the repository root.

```sh
psql -X -v ON_ERROR_STOP=1 -f infra/postgres/migrations/0001_ingestion_acceptance.sql
```

Continue with `0002_source_events.sql`, `0003_publication.sql` and `0004_private_acl.sql` using the same command. Start at `0002` on an existing v1 schema. Each file has its own transaction. The event migration preserves historical receipts.
