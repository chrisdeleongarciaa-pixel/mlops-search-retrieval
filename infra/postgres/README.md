# PostgreSQL

PostgreSQL owns per-document order and desired revision. The adapter serializes writes with a per-document transaction lock and compares the complete identity. A lock-key collision can serialize distinct documents. Full identity comparison preserves their separation.

Revision, desired pointer and outbox share one commit. Outbox `event_id` identifies the revision and operation, and `document_key` resolves source scope. Acceptance sets `synchronous_commit=on`. The deployment must provide persistent WAL and verified durability settings. [Migrations](migrations/README.md) · [Publication decision](../../engineering/decisions/0001-publicacion.md)
