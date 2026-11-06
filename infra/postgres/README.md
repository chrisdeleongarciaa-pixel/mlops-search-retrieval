# PostgreSQL

PostgreSQL conserva el orden de aceptación y la revisión deseada de cada documento. El adaptador serializa las escrituras con un bloqueo transaccional por documento, compara la identidad completa y confirma la revisión, el estado deseado y la outbox en un solo commit. Una colisión de la clave del bloqueo puede serializar documentos distintos. La comparación de los identificadores completos conserva su separación.

Todas las escrituras en las tablas de aceptación deben respetar el protocolo del adaptador. El evento de outbox se vincula por `event_id` a la revisión, que conserva la operación y la secuencia. El consumidor puede obtener el ámbito y el identificador del documento a través de `document_key`. La aceptación configura `synchronous_commit = on` en su transacción. El entorno debe proporcionar persistencia de WAL y una configuración de durabilidad adecuada.

Consulta las [migraciones](migrations/README.md) y la [decisión de publicación](../../docs/decisions/0001-publicacion.md).
