BEGIN;

CREATE TABLE ingestion_documents (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lock_key bigint NOT NULL,
    tenant_id text COLLATE "C" NOT NULL,
    source_id text COLLATE "C" NOT NULL,
    document_id text COLLATE "C" NOT NULL,
    desired_sequence bigint
);

CREATE INDEX ingestion_documents_lock_key_idx ON ingestion_documents (lock_key);

CREATE TABLE ingestion_revisions (
    document_key bigint NOT NULL REFERENCES ingestion_documents (id),
    sequence bigint NOT NULL,
    source_position bigint NOT NULL,
    schema_version integer NOT NULL CHECK (schema_version = 1),
    operation text NOT NULL CHECK (operation IN ('UPSERT', 'WITHDRAW')),
    source_revision text,
    original_size bigint,
    original_sha256 text COLLATE "C",
    original_ref text,
    event_id uuid NOT NULL UNIQUE,
    accepted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (document_key, sequence),
    UNIQUE (document_key, source_position),
    CHECK (sequence > 0 AND sequence <= source_position),
    CHECK (
        (operation = 'UPSERT'
            AND original_size IS NOT NULL AND original_size >= 0
            AND original_sha256 IS NOT NULL AND original_sha256 ~ '^[0-9a-f]{64}$'
            AND original_ref IS NOT NULL AND original_ref <> '')
        OR
        (operation = 'WITHDRAW'
            AND original_size IS NULL AND original_sha256 IS NULL AND original_ref IS NULL)
    )
);

ALTER TABLE ingestion_documents
    ADD FOREIGN KEY (id, desired_sequence)
    REFERENCES ingestion_revisions (document_key, sequence);

-- The immutable revision supplies the event payload by event_id.
CREATE TABLE ingestion_outbox (
    event_id uuid PRIMARY KEY REFERENCES ingestion_revisions (event_id)
);

COMMIT;
