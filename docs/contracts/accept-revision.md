# Contrato de aceptación durable de revisiones

**Versión** · 1  
**Estado** · Implementado para el perfil de ingesta con PostgreSQL y almacén de archivos.  
**Operación interna** · `accept_revision(command) → receipt | outcome`

## Alcance

El recibo certifica que una revisión documental quedó aceptada de forma durable y que su procesamiento se registró en la outbox. La revisión deseada es la última aceptada. El estado de búsqueda y el acceso dependen de los contratos de [publicación](../decisions/0001-publicacion.md) y [autorización](../decisions/0002-autorizacion.md).

## AC-01 · Entrada y precondición

La frontera autentica al conector y comprueba que puede escribir en el tenant y la fuente autorizados para esa invocación. Construye `DocumentKey` con ese ámbito autenticado y el identificador documental validado. Si el sobre repite el ámbito, debe coincidir con el autorizado. La frontera valida explícitamente el sobre antes de construir el comando que recibe el caso de uso.

La interfaz Python recibe ese ámbito desde un conector o transporte de confianza. El [punto de entrada de ingesta](../../src/search_retrieval/ingestion/README.md) define el sobre y el stream de entrada.

| Campo | Regla para la versión 1 |
| --- | --- |
| `schema_version` | Entero `1`. |
| `document` | `DocumentKey(tenant_id, source_id, document_id)`. Los identificadores son cadenas Unicode válidas, con al menos un carácter distinto de espacio en blanco y sin NUL. Se comparan tal como se validaron. |
| `source_position` | Entero de 1 a 2⁶³−1. Es estable para los reintentos y ordena estrictamente los cambios de cada documento en su fuente. El conector debe preservar ese orden incluso cuando la entrega se reordena. |
| `source_revision` | Etiqueta opaca opcional de la fuente. Cuando se aporta, se aplican las reglas de cadena de los identificadores. Se usa para procedencia e igualdad, con independencia de su valor lexicográfico. |
| `operation` | `UPSERT` o `WITHDRAW`. |
| `original` | En `UPSERT`, bytes del original y SHA-256 esperado. Se acepta un cuerpo vacío con su digest correcto. En `WITHDRAW`, este campo está ausente. |

El límite de bytes es un parámetro obligatorio y positivo del entorno de ingesta. Se aplica durante la lectura del original, incluso si falta o es errónea la longitud declarada por el transporte. Los booleanos son inválidos en campos enteros. El digest se representa como 64 caracteres hexadecimales y se normaliza a minúsculas antes de compararlo. La identidad autenticada y los controles obligatorios se comprueban con validaciones explícitas.

Un conector con tokens opacos de revisión debe aportar por separado una posición estable con el orden de origen. Cuando una fuente reinicia su numeración, el conector debe preservar la continuidad de posiciones para el mismo documento antes de admitir cambios de esa fuente.

## AC-02 · Idempotencia y equivalencia

La clave lógica de idempotencia es `(DocumentKey, source_position)`. A igualdad de clave, dos comandos son equivalentes si coinciden `schema_version`, `operation` y `source_revision`, y, en un `UPSERT`, también el tamaño efectivo y el SHA-256 comprobado de los bytes. La ausencia de `source_revision` equivale a otra ausencia. Los identificadores de solicitud y las marcas de tiempo de transporte quedan fuera de la comparación.

Para un original reenviado, la igualdad se comprueba contra los bytes recibidos y su digest calculado. Un digest declarado idéntico con bytes distintos produce un error de integridad si el digest calculado difiere. Tras validar el comando y sus bytes, la consulta del registro durable permite resolver un reintento ya aceptado sin acceder al objeto histórico. La revisión aceptada conserva los campos de equivalencia y el recibo. La versión 1 conserva ese registro y la marca de orden sin expiración automática. Cualquier purga requiere definir primero la ventana de reintento y cómo se conserva el orden.

## AC-03 · Postcondición y recibo

En `UPSERT`, antes de confirmar la transacción, el almacén debe poder verificar la identidad inmutable, el tamaño y el SHA-256 del objeto persistido. La verificación se basa en el contenido almacenado o en un checksum calculado y verificado por el almacén. El objeto debe seguir disponible al confirmar la revisión. Toda limpieza concurrente debe preservar los objetos que respaldan una aceptación en curso o una referencia vigente. En `WITHDRAW`, la nueva revisión registra el retiro como estado deseado, sin referencia a original.

Una transacción PostgreSQL confirma el registro de revisión con los datos del recibo, la actualización de la revisión deseada y un único evento lógico de outbox vinculado a esa revisión. El evento identifica el documento, la secuencia interna y la operación. El recibo se construye con la clave documental, la posición de fuente, la secuencia interna, la operación, el identificador estable del evento y el instante de aceptación persistido. Solo se emite un recibo tras confirmar el commit o comprobar una aceptación ya persistida.

Una repetición equivalente devuelve los mismos campos del recibo persistido. El recibo es histórico. Su devolución tras otra aceptación posterior no acredita la disponibilidad actual del original ni el estado de publicación.

## AC-04 · Orden e invariantes

El estado durable de un documento guarda la posición más alta aceptada y su revisión deseada. Inicialmente, el documento carece de revisiones y la marca de posición es 0. Cada aceptación nueva recibe la secuencia interna anterior más uno, empezando por 1. Pueden existir huecos en las posiciones de la fuente.

La decisión durable aplica este orden bajo una garantía de serialización por documento, también cuando dos operaciones intentan crearlo por primera vez.

1. Si la clave tiene una aceptación previa y el comando es equivalente, devuelve el recibo persistido sin modificar la revisión deseada.
2. Si la clave tiene una aceptación previa y el comando difiere, devuelve `IDEMPOTENCY_CONFLICT`.
3. Si la clave carece de aceptación previa y su posición es menor o igual que la marca de orden, devuelve `STALE_REVISION`.
4. En los demás casos, registra la revisión y el evento y avanza la revisión deseada y la marca de orden en la misma transacción.

Después de cada commit, existe una sola revisión aceptada por clave y una sola identidad de evento de aceptación asociada a ella. La revisión deseada corresponde siempre a la mayor posición aceptada del documento, incluso si esa revisión es un retiro. Estos invariantes son sobre el estado de aceptación. La entrega del evento al consumidor puede repetirse.

## AC-05 · Fallos y reintentos

| Resultado | Condición observable |
| --- | --- |
| `INVALID_ENVELOPE` | Sobre inválido, versión no soportada, operación incompatible con el cuerpo o exceso del límite de bytes. |
| `NOT_AUTHORIZED` | La identidad de ingesta carece de acceso al ámbito solicitado. |
| `INTEGRITY_ERROR` | El digest de los bytes recibidos difiere del esperado o el objeto persistido carece de la integridad requerida. |
| `IDEMPOTENCY_CONFLICT` | La clave identifica una aceptación previa con otro contenido semántico. |
| `STALE_REVISION` | La clave aún carece de aceptación y su posición queda detrás de la marca, o coincide con ella. |
| `DEPENDENCY_FAILURE` | Una dependencia falla antes de intentar el commit o se confirma que la transacción se revirtió. |
| `OUTCOME_UNKNOWN` | Se pierde la confirmación del commit y todavía se desconoce el resultado durable. El llamador puede reenviar el mismo comando. |

Estos resultados carecen de recibo de éxito. Una transacción revertida deja intactos el registro de aceptación, la revisión deseada y la outbox. Si ya se escribió un objeto y quedó sin referencias, podrá reconciliarse como huérfano. El mismo resultado puede ocurrir cuando una escritura de objeto antecede a una decisión concurrente de conflicto o revisión obsoleta. Cualquier limpieza exige comprobar referencias y coordinarse con las aceptaciones en curso.

Ante confirmación incierta, una nueva lectura puede recuperar una aceptación existente. Una lectura que aún no observa la fila es insuficiente para declarar revertida una transacción cuyo resultado sigue abierto. El reintento con la misma clave vuelve a resolver la decisión bajo la garantía de serialización.

## Verificación

La [matriz de verificación de aceptación](../verification/accept-revision.md) enlaza cada cláusula con su oráculo y los casos que deben ejecutarse.
