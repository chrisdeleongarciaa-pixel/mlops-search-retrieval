BEGIN;

CREATE TABLE retrieval_private_documents (
    document_key bigint PRIMARY KEY REFERENCES ingestion_documents (id)
);

CREATE TABLE retrieval_acl (
    document_key bigint NOT NULL REFERENCES retrieval_private_documents (document_key),
    principal text COLLATE "C" NOT NULL,
    PRIMARY KEY (document_key, principal)
);

COMMIT;
