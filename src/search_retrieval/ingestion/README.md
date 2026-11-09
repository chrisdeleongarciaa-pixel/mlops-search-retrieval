# Ingestion use cases

Connectors translate source changes to authorized envelopes. The package verifies originals, accepts revisions durably and publishes projections through an outbox. [Acceptance contract](../../../docs/contracts/accept-revision.md) · [Verification](../../../docs/verification/accept-revision.md)

Install `.[postgres]`, apply the [migrations](../../../infra/postgres/migrations/README.md) and configure a persistent [originals directory](../../../infra/object-storage/README.md). The trusted connector or transport supplies `AuthorizedSource`. The caller retains ownership of its binary stream. Acceptance checks effective size and SHA-256 and uses temporary storage beyond its in-memory threshold.

```python
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path

from search_retrieval.adapters.file_objects import FileObjectStore
from search_retrieval.adapters.postgres_acceptance import PostgresAcceptanceLedger
from search_retrieval.ingestion.accept_revision import AuthorizedSource, RevisionAcceptance

acceptance = RevisionAcceptance(
    PostgresAcceptanceLedger(os.environ["INGESTION_DSN"]),
    FileObjectStore(Path(os.environ["INGESTION_OBJECTS"])),
    max_original_bytes=int(os.environ["INGESTION_MAX_ORIGINAL_BYTES"]),
)
scope = AuthorizedSource("tenant-demo", "source-demo")
body = b"original"
result = acceptance.accept_revision(
    scope,
    {
        "schema_version": 1,
        "document_id": "doc-1",
        "source_position": 7,
        "operation": "UPSERT",
        "original_sha256": sha256(body).hexdigest(),
    },
    BytesIO(body),
)
```

The operation returns a `Receipt` or a `Failure`. Omit stream and original hash for `WITHDRAW`. Retry the same command after `OUTCOME_UNKNOWN`. Configure PostgreSQL durability and deadlines and reserve space for temporary originals. The worker retries independently and activates through the [publication protocol](../../../engineering/decisions/0001-publicacion.md).
