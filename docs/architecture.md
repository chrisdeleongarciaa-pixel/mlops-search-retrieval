# Arquitectura

## Límites de código

El repositorio distribuye un paquete Python. Ingesta y consulta son módulos con casos de uso propios. Pueden tener puntos de arranque y perfiles de recursos independientes a medida que se implementen. Cada caso de uso define las interfaces que necesita. Los adaptadores implementan esas interfaces y el arranque conecta las implementaciones con sus consumidores.

- `contracts` contiene identidades compartidas y depende de la biblioteca estándar.
- `ingestion` y `query` comparten identidades. Su coordinación pasa por el estado de publicación y las interfaces declaradas.
- `adapters` traduce entre las interfaces internas y los protocolos externos. Los casos de uso se mantienen independientes de esos clientes.
- `evaluation` consume conjuntos de datos, manifiestos y salidas observables. Sus oráculos se definen de forma independiente de los algoritmos evaluados.

## Autoridad e interfaces

| Componente | Entrada y salida en el límite | Estado que controla |
| --- | --- | --- |
| Aceptación · PostgreSQL y almacén de objetos | Evento con identidad de fuente y checksum → recibo durable | Orden de revisiones, bytes originales y evento de outbox. |
| Transformación · GROBID y tokenizer | Original aceptado y receta fijada → TEI, texto canónico y manifiesto | Identidad de transformación y localizadores de fragmentos. |
| Publicación · PostgreSQL y Vespa | Proyección candidata consultable → activación condicionada | PostgreSQL decide elegibilidad. Vespa sirve proyecciones reconstruibles. |
| Recuperación · Vespa y autoridad de acceso | Consulta, identidad y presupuesto → candidatos elegibles | Generación y espacio vectorial de la consulta. La autoridad de acceso aporta la decisión vigente. |
| Contexto y entrega · casos de uso y adaptador vLLM | Candidatos revalidados → contexto acotado → respuesta con citas comprobadas | Selección efectiva de fragmentos y decisión de entrega. |

La aceptación registra revisión y outbox en una transacción. El worker de publicación verifica que la proyección candidata es consultable antes de solicitar su activación. La revisión previamente publicada conserva continuidad durante una actualización ordinaria. El retiro cambia la elegibilidad en la autoridad y la activación posterior queda condicionada al estado vigente. Estas propiedades se ensayaron con proyecciones testigo. La conexión de la receta PDF con el worker de publicación tiene su propio criterio de aceptación. [Aceptación](contracts/accept-revision.md) · [Publicación](decisions/0001-publicacion.md) · [Transformación PDF](contracts/scientific-pdf.md)

La elegibilidad se aplica antes del límite de candidatos. El contexto identifica cada fragmento por revisión y transformación. El testigo verifica revalidación y citas contra el contexto efectivo. La integración de estas operaciones con un transporte autenticado y vLLM queda registrada como trabajo pendiente del servicio. [Contrato de consulta y entrega](contracts/witness-increments.md) · [Registro de configuración](checkpoints/2026-11-08.md)

## Identidad y evolución

`DocumentKey` combina tenant, fuente y documento. `RevisionKey` añade una secuencia interna por documento. Un ETag de origen puede conservarse como dato opaco. `ChunkKey` añade una transformación identificada y el fragmento producido por ella. Los límites de entrada validarán tipos y semántica antes de construir estas claves.

El [manifiesto de versión](../config/release.example.toml) vincula el código, el corpus, las transformaciones, el espacio vectorial, el índice, el ranking, el modelo generativo y las políticas. Identificar cada parte permite reconstruir y comparar ejecuciones. Las [decisiones pendientes](decisions/README.md) enumeran las pruebas que condicionan la integración de los flujos.

La [delimitación y los objetivos de aceptación](operations/service-objectives.md) fijan las puertas de recuperación, calidad, latencia, revocación y recuperación ante fallos.
