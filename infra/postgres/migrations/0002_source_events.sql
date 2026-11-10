BEGIN;

CREATE TABLE ingestion_source_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lookup_key bytea NOT NULL,
    tenant_id text COLLATE "C" NOT NULL,
    source_id text COLLATE "C" NOT NULL,
    source_event_id text COLLATE "C" NOT NULL,
    semantic jsonb NOT NULL,
    receipt_event_id uuid UNIQUE REFERENCES ingestion_revisions (event_id)
);

CREATE INDEX ingestion_source_events_lookup_idx ON ingestion_source_events (lookup_key);

CREATE TABLE ingestion_witness_checkpoints (
    feed_sha256 text COLLATE "C" PRIMARY KEY CHECK (feed_sha256 ~ '^[0-9a-f]{64}$'),
    cursor bigint NOT NULL CHECK (cursor >= 0)
);

COMMIT;
