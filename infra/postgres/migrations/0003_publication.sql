BEGIN;

CREATE TABLE retrieval_generation (
    singleton boolean PRIMARY KEY CHECK (singleton),
    active text COLLATE "C" NOT NULL
);
INSERT INTO retrieval_generation VALUES (true, 'witness-v1');

CREATE TABLE retrieval_publications (
    document_key bigint NOT NULL,
    generation text COLLATE "C" NOT NULL,
    sequence bigint NOT NULL,
    projection_id text COLLATE "C" NOT NULL UNIQUE,
    PRIMARY KEY (document_key, generation),
    FOREIGN KEY (document_key, sequence) REFERENCES ingestion_revisions (document_key, sequence)
);

CREATE TABLE retrieval_attempts (
    event_id uuid PRIMARY KEY REFERENCES ingestion_outbox (event_id),
    token uuid NOT NULL,
    generation text COLLATE "C" NOT NULL,
    expires_at timestamptz NOT NULL,
    attempts bigint NOT NULL CHECK (attempts > 0),
    outcome text CHECK (outcome IN ('published', 'obsolete', 'withdrawn', 'quarantined'))
);

CREATE TABLE retrieval_payloads (
    projection_id text COLLATE "C" PRIMARY KEY,
    sha256 text COLLATE "C" NOT NULL,
    manifest jsonb NOT NULL
);

CREATE INDEX ingestion_revisions_withdrawals_idx
    ON ingestion_revisions (document_key, sequence) WHERE operation = 'WITHDRAW';

COMMIT;
