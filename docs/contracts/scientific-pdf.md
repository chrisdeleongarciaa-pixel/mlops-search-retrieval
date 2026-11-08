# Perfil PDF científico v1

El incremento procesa PDF con texto seleccionable mediante GROBID 0.9.1 CRF. La operación de transformación recibe una revisión durable y produce artefactos de una generación candidata. Su siguiente consumidor será la publicación condicional existente.

| ID | Precondición | Postcondición e invariante |
| --- | --- | --- |
| PDF-01 | Referencia a revisión, SHA-256 del original y receta identificados. PDF de hasta 20 MiB y 200 páginas. | Se verifican bytes y formato antes de extraer. Corrupción, PDF cifrado, ausencia de texto, límite y fallo temporal tienen códigos distintos. |
| PDF-02 | TEI completo y acotado producido por el parser fijado. | Se conservan TEI y texto canónico UTF-8. Encabezados y párrafos de abstract y cuerpo conservan sección y todas las cajas del elemento original. Figuras, tablas y bibliografía quedan excluidas en este perfil. |
| PDF-03 | Contador del tokenizer identificado que incluye prompt y tokens especiales. | Cada fragmento cumple el presupuesto efectivo de 512 tokens, resuelve un rango semiabierto del texto canónico y verifica su SHA-256. La división conserva todas las cajas del elemento, con granularidad de párrafo declarada. |
| PDF-04 | Mismos bytes, revisión, TEI, receta y tokenizer. | Identidad y manifiesto deterministas. Cambiar receta o generación cambia la identidad. Un resultado parcial o rechazado carece de un manifiesto completo para publicación. |
| PDF-05 | Directorio de salida nuevo y acceso exclusivo del operador. | Los artefactos se preparan en staging. El manifiesto y el resultado terminal se completan antes del renombrado. Una ejecución fallida conserva un resultado de rechazo y los insumos identificados. |

La receta normaliza el espacio interno de cada elemento y une bloques con dos saltos de línea. Los offsets corresponden al texto canónico, y las cajas a coordenadas PDF emitidas por GROBID. Si falta una caja se declara `unavailable`. Los fragmentos divididos conservan el localizador del elemento entero. La calidad de lectura, el orden de columnas y la precisión de cajas se validan sobre el PDF y las anotaciones independientes.

Se comienza con párrafos independientes, sin solapamiento y sin combinar secciones. Un párrafo largo se divide en límites de espacio con conteo efectivo del tokenizer. Una unidad indivisible que supera el presupuesto se rechaza explícitamente. La receta, el límite y el contador forman parte de la identidad. El contador de palabras del fixture comprueba invariantes de fragmentación. La ejecución real usa el tokenizer fijado de E5 o EmbeddingGemma.

El cliente acota bytes, páginas, TEI y tiempo de transporte. El contenedor acota CPU, RAM y concurrencia. La cancelación de la conexión HTTP y la terminación del procesamiento remoto requieren evidencia distinta. Un timeout no acredita liberación del worker remoto.

Este perfil aplica la estructura de requisitos, interfaces, trazabilidad y verificación del diseño. La evaluación normativa completa de ISO/IEC/IEEE requiere los textos y el procedimiento de auditoría correspondientes.

[Contrato de transformación original](reference/v1/CONTRATOS_Y_ENTORNO_REPRODUCIBLE_RETRIEVAL.md) · [API GROBID](https://grobid.readthedocs.io/en/latest/Grobid-service/) · [Coordenadas](https://grobid.readthedocs.io/en/latest/Coordinates-in-PDF/)
