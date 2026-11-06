# Recuperación densa e híbrida del laboratorio

| Operación | Precondición | Postcondición e invariante observable |
| --- | --- | --- |
| Codificar con E5 | Snapshot de artefactos con hashes verificados, rol de consulta o pasaje y entorno FP32 | Un vector finito y normalizado de 768 componentes por entrada, en el mismo orden. La máscara excluye el padding del promedio. El límite de 512 incluye prefijo y tokens especiales. |
| Proyectar un fragmento | Revisión durable y generación habilitada para un espacio vectorial fijo | Se verifica el conjunto de fragmentos por la ruta de búsqueda antes de activar la proyección. La identidad de la proyección incluye el espacio. El registro en PostgreSQL conserva procedencia y controla la activación. |
| Recuperar candidatos | Identidad de tenant, consulta, presupuesto y generación compatibles | Se rankea dentro de la elegibilidad. Una segunda lectura confirma el snapshot antes de devolver resultados. Tras un cambio se permite un reintento dentro del plazo. Un snapshot vacío compatible produce un vacío válido. Una cobertura parcial, un vector ausente o una generación incompatible producen un error explícito. |
| Fusionar | Dos ramas completas para el mismo snapshot | RRF usa hasta 100 candidatos por rama, pesos iguales y constante 60, con desempate por ID. Antes de reutilizar el resultado en contexto se vuelve a consultar la autoridad. |

La prueba mutable admite hasta 400 fragmentos elegibles por solicitud para verificar presencia y espacio en Vespa. La medición registra ese coste por separado. El corpus SciFact usa una generación inmutable y se valida al completar la carga. Sus filtros selectivos enumeran las identidades del subconjunto antes de buscar vecinos.

El retiro es una propiedad del protocolo de publicación y consulta. Un trabajador puede escribir una proyección física después del retiro. La actualización condicional de PostgreSQL impide que esa proyección se haga elegible y la revalidación excluye evidencia retirada de un resultado previo.

El oráculo SciFact usa coseno FP64 de los vectores almacenados en `float32`, con desempate por ID. Vespa usa `prenormalized-angular` con la expresión `1 - distance(field, embedding)`. El umbral de `1e-5` separa intercambios de corte por precisión de cambios sustantivos. La medida estricta conserva toda diferencia de IDs.

[Perfil de aceptación v1](../operations/service-objectives.md) · [Trazabilidad y reproducción](../verification/dense-retrieval.md)
