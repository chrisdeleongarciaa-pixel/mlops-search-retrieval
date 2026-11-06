# Ingesta

Aquí viven los casos de uso para aceptar cambios de una fuente, conservar originales, transformar revisiones y publicar proyecciones. Los conectores traducen sus cambios a la interfaz de aceptación. El primer caso de uso sigue el [contrato de aceptación durable](../../../docs/contracts/accept-revision.md) y su [matriz de verificación](../../../docs/verification/accept-revision.md).

## Aceptar una revisión

Instala el extra `postgres` con `python -m pip install -e ".[postgres]"`, aplica la [migración inicial](../../../infra/postgres/migrations/README.md) y prepara un [directorio persistente de originales](../../../infra/object-storage/README.md). El punto de entrada es `RevisionAcceptance.accept_revision(scope, envelope, original)`.

El conector o transporte autentica al llamador y proporciona un `AuthorizedSource` del ámbito autorizado. El sobre puede repetir el ámbito para comprobarlo. El original es un stream binario que conserva la propiedad del llamador. El servicio valida su longitud efectiva y SHA-256 y utiliza archivos temporales para los cuerpos que superan el umbral de memoria.

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

# Ejemplo local. En un conector, el ámbito viene de la identidad autorizada.
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

El resultado es un `Receipt` o un `Failure` del contrato. Para `WITHDRAW` se omiten el stream y `original_sha256`. Ante `OUTCOME_UNKNOWN`, el conector reenvía el mismo comando para reconciliar el resultado. Para gestionar el despliegue, configura los tiempos de espera y la durabilidad de PostgreSQL y el espacio temporal de ingesta.

El worker podrá reintentar cada evento y registrar resultados idempotentes. La publicación, la verificación de visibilidad y la retirada requieren el contrato de la [decisión 0001](../../../docs/decisions/0001-publicacion.md).
