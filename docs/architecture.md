# Arquitectura

## Límites de código

El repositorio distribuye un paquete Python. Ingesta y consulta son módulos con casos de uso propios. Pueden tener puntos de arranque y perfiles de recursos independientes a medida que se implementen. Cada caso de uso define las interfaces que necesita. Los adaptadores implementan esas interfaces y el arranque conecta las implementaciones con sus consumidores.

- `contracts` contiene identidades compartidas y depende de la biblioteca estándar.
- `ingestion` y `query` comparten identidades. Su coordinación pasa por el estado de publicación y las interfaces declaradas.
- `adapters` traduce entre las interfaces internas y los protocolos externos. Los casos de uso se mantienen independientes de esos clientes.
- `evaluation` consume conjuntos de datos, manifiestos y salidas observables. Sus oráculos se definen de forma independiente de los algoritmos evaluados.

## Estado y flujos

| Componente | Responsabilidad |
| --- | --- |
| PostgreSQL | Orden interno de revisiones, estado de publicación, retiros y outbox. |
| Almacén de objetos | Originales y artefactos inmutables con checksum y procedencia. |
| Vespa | Proyecciones léxicas y densas reconstruibles. |
| Gateway | Identidad autenticada, presupuestos y política de entrega. |
| vLLM | Inferencia con un modelo, tokenizer y plantilla identificados. |

En ingesta, el [contrato de aceptación durable](contracts/accept-revision.md) fija el punto de confirmación, los reintentos y el orden por documento. El worker procesa una revisión identificada y verifica que su proyección se puede consultar antes de solicitar su activación. La [decisión de publicación](decisions/0001-publicacion.md) define el contrato que debe preservar la continuidad durante ese cambio.

En consulta, la identidad autenticada acota la recuperación. La elegibilidad de revisiones y permisos debe aplicarse conforme a los contratos antes de seleccionar evidencia. El contexto identifica cada fragmento por revisión y transformación. La respuesta se entrega según la política de autorización y citas adoptada.

## Identidad y evolución

`DocumentKey` combina tenant, fuente y documento. `RevisionKey` añade una secuencia interna por documento. Un ETag de origen puede conservarse como dato opaco. `ChunkKey` añade una transformación identificada y el fragmento producido por ella. Los límites de entrada validarán tipos y semántica antes de construir estas claves.

El [manifiesto de versión](../config/release.example.toml) vincula el código, el corpus, las transformaciones, el espacio vectorial, el índice, el ranking, el modelo generativo y las políticas. Identificar cada parte permite reconstruir y comparar ejecuciones. Las [decisiones pendientes](decisions/README.md) enumeran las pruebas que condicionan la integración de los flujos.

La [delimitación y los objetivos de aceptación](operations/service-objectives.md) fijan las puertas de recuperación, calidad, latencia, revocación y recuperación ante fallos.
