# Durable acceptance verification

The acceptance matrix pairs [AC-01–AC-05](../contracts/accept-revision.md) with observable oracles. PostgreSQL tests query the ledger independently and verify the persisted original by SHA-256. Generated histories use an independent state model. Test doubles isolate boundary behavior and do not substitute for commit or restart tests.

| Case | Clauses | Stimulus | Expected observation |
| --- | --- | --- | --- |
| AV-01 | AC-01, AC-05 | Invalid schema, identity, positions, body, size or scope | Explicit rejection before acceptance or event. |
| AV-02 | AC-01, AC-03, AC-05 | Incoming or persisted checksum mismatch | Integrity error, with ledger and outbox unchanged. |
| AV-03 | AC-03, AC-04 | Valid original for a new document | Verified original, sequence 1, desired pointer and one logical outbox event. |
| AV-04 | AC-02–AC-05 | Kill after commit before receipt, restart and retry | The existing acceptance is observable before retry. Retry returns the persisted receipt. |
| AV-05 | AC-03, AC-05 | Roll back while recording outbox after object verification | Desired pointer and ledger unchanged. New object may remain unreferenced. |
| AV-06 | AC-02, AC-04, AC-05 | Same accepted key with changed content | Conflict. Preserve original receipt, pointer and event. |
| AV-07 | AC-02, AC-04 | Accept positions 7 and 9, replay 7 and then submit unseen 8 | Recover position 7 receipt, reject 8 as stale and retain 9 as desired. |
| AV-08 | AC-02, AC-04 | Concurrent first writes to same position | Equivalent calls share the receipt. Conflicting calls leave one acceptance and one event. |
| AV-09 | AC-04 | Force both durable orders for positions 7 and 8 | Position 8 remains desired. Earlier accepted 7 has its own event, otherwise it is stale. |
| AV-10 | AC-03, AC-04 | Upsert, withdraw, replay old upsert | Return historical upsert receipt while withdrawal remains desired. |
| AV-11 | AC-01, AC-02, AC-04 | Same local ID and position under separate tenant/source scopes | Independent revision and event identities, with isolation across scope. |
| AV-12 | AC-05 | Lose commit acknowledgement for a commit and a rollback | Unresolved outcome until retry. One durable acceptance once state is stable. |
| AV-13 | AC-03, AC-05 | Cut before store verification and after object write before transaction | Ledger unchanged, with possible orphan in the second case. Retry creates one acceptance. |

## Recovery witness AV-04

The child signals after durable commit and before writing the receipt. The parent terminates it, reads PostgreSQL and the object store, then starts a new process and retries. The test compares the returned receipt fields to the persisted sequence, event ID and time. [PostgreSQL test](../../tests/integration/test_accept_revision_postgres.py)

The automatic CPU gate runs the 12 PostgreSQL acceptance cases plus the boundary suite. It verifies that all selected tests ran and returned the terminal `OK`. [Integration gate](integration-gate.md) · [Reproduction instructions](../../tests/integration/README.md)
