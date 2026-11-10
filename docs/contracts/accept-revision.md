# Durable revision acceptance

**Version 1** · PostgreSQL and a file-backed object store

**Internal operation** · `accept_revision(command) → receipt | outcome`

A receipt certifies durable acceptance of a revision and its logical outbox event. The desired revision is the latest accepted revision. Search visibility and access follow the publication and authorization protocols. [Publication](../../engineering/decisions/0001-publicacion.md) · [Authorization](../../engineering/decisions/0002-autorizacion.md)

## AC-01 · Input and precondition

The boundary authenticates the connector and validates access to the invocation's tenant and source. It constructs the document key from that scope and the validated document ID. A repeated scope in the envelope must match the authenticated scope. The Python entry point accepts scope from a trusted connector or transport. [Envelope and stream interface](../../src/search_retrieval/ingestion/README.md)

| Field | Version 1 rule |
| --- | --- |
| `schema_version` | Integer `1`. |
| `document` | `DocumentKey(tenant_id, source_id, document_id)`. Each ID is valid Unicode with a non-whitespace character and no NUL. Equality uses the validated string as supplied. |
| `source_position` | Integer from 1 to 2⁶³−1. The connector preserves the per-document source order and reuses the position for retries, including out-of-order delivery. |
| `source_revision` | Optional opaque label subject to the identifier string rules. It participates in equality and provenance. |
| `operation` | `UPSERT` or `WITHDRAW`. |
| `original` | `UPSERT` supplies bytes and an expected SHA-256, including the valid empty-body case. `WITHDRAW` requires the field to be absent. |

A positive byte limit applies while reading, regardless of any declared transport length. Integer fields exclude booleans. The digest has 64 hex characters and is normalized to lowercase. Input, identity and integrity checks are explicit. A connector using opaque revision tokens supplies a separate ordered position and preserves continuity across source counter resets.

## AC-02 · Idempotency and equivalence

The logical key is `(DocumentKey, source_position)`. With the same key, equivalence requires the same schema version, operation and source revision, plus the effective byte count and verified SHA-256 for an upsert. An absent source revision equals another absence. Transport request IDs and timestamps are outside equivalence.

Retries validate incoming bytes and then read the durable acceptance record. An accepted retry can resolve without reading the historical object. The record retains equivalence fields and the persisted receipt. V1 retains its order watermark and acceptance history indefinitely. Purging requires a retry window and an order preservation rule.

## AC-03 · Postcondition and receipt

Before committing an upsert, the store must verify immutable identity, size and SHA-256 from persisted content or a verified store checksum. The object remains available through commit. Concurrent cleanup preserves objects referenced by in-flight acceptance and live revisions. A withdrawal records a desired revision with no original reference.

One PostgreSQL transaction commits the revision and receipt fields, advances the desired pointer and records one logical outbox event. The receipt contains document key, source position, internal sequence, operation, stable event ID and persisted acceptance time. Return it only after commit confirmation or an independent read of a persisted acceptance.

Equivalent repeats return the same receipt fields. The receipt describes the historical acceptance. Current object availability and search visibility are separate state.

## AC-04 · Order and invariants

The durable state starts with watermark 0. Each new acceptance increments the internal sequence from 1. Source positions can have gaps. Serialize the following decision per document, including concurrent first writes.

1. An existing key with equivalent content returns its receipt and leaves the desired pointer unchanged.
2. An existing key with different content returns `IDEMPOTENCY_CONFLICT`.
3. An unseen key at or below the watermark returns `STALE_REVISION`.
4. Otherwise the transaction records the revision and outbox event and advances the pointer and watermark.

After commit, each accepted key has one revision and one logical event identity. The desired revision always corresponds to the highest accepted position, including a withdrawal. Consumer delivery can repeat.

## AC-05 · Failure and retry

| Outcome | Observable condition |
| --- | --- |
| `INVALID_ENVELOPE` | Invalid fields, unsupported version, inconsistent operation and body, or exceeded byte limit. |
| `NOT_AUTHORIZED` | Connector lacks access to the scope. |
| `INTEGRITY_ERROR` | The received digest differs from the expected digest or persisted content fails verification. |
| `IDEMPOTENCY_CONFLICT` | The key identifies a different prior acceptance. |
| `STALE_REVISION` | The unseen key is at or below the accepted watermark. |
| `DEPENDENCY_FAILURE` | Failure before commit or confirmed rollback. |
| `OUTCOME_UNKNOWN` | Commit acknowledgement was lost and the durable result is unresolved. Retry the same command. |

A failed or unresolved call carries no successful receipt. Rollback leaves the acceptance record, desired pointer and outbox unchanged. An earlier object write can leave an orphan after rollback, conflict or staleness. Cleanup requires checking references and coordinating with in-flight accepts. After an ambiguous commit, an absent row alone cannot establish rollback. A same-key retry resolves the decision under per-document serialization. [Verification matrix](../verification/accept-revision.md)
