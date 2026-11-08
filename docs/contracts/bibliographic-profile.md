# Perfil bibliográfico y comparación de reescritura v1

El dominio adoptado es búsqueda de literatura ML/NLP en inglés. El documento de recuperación es un artículo representado como `título + U+0020 + abstract`; la relevancia se juzga por artículo. Español queda fuera del alcance de este perfil. Los fixtures históricos conservan sus bytes e identidades para mantener las regresiones.

LitSearch es el conjunto de consultas bibliográficas del dominio. SciFact se conserva como benchmark separado y regresión reproducible. Sus respectivos conjuntos de 300 consultas tienen IDs, juicios y significados distintos. La comparación original/Qwen/Gemma se prepara sobre LitSearch-300 y no atribuye resultados de SciFact a este dominio.

| ID | Obligación de entrada | Garantía y verificación |
| --- | --- | --- |
| BIB-01 | Siete entradas del snapshot fijado, tamaños y SHA-256 correctos. | 64.183 artículos únicos y 597 consultas con positivos resolubles. Se materializan 300 consultas por cuotas de procedencia/especificidad y orden SHA-256 con semilla 20261108. `evaluation/test_litsearch.py` verifica estabilidad ante permutación. |
| BIB-02 | Congelar la selección antes de ejecutar rankings. | Desarrollo excluye componentes conectados por positivos compartidos o texto de consulta idéntico normalizado. Se conservan IDs y hashes del conjunto. Este aislamiento no acredita separación temática completa ni elimina posible contaminación del preentrenamiento. |
| BIB-03 | Ranking completo por ID de consulta, sin candidatos duplicados. | Recall@5 y Recall@20 se calculan contra positivos heredados. Un documento sin juicio permanece no juzgado; no se convierte en negativo. Casos manuales verifican recall y rechazo de cobertura incompleta. |
| RW-01 | Consulta original válida; JSON completo con `finish_reason=stop`. | Se rechazan campos extra/duplicados, acciones inválidas, texto vacío y salida truncada. La acción `keep` conserva exactamente el original. |
| RW-02 | Literales obligatorios explícitos, presentes en la consulta original. | Se comprueba su conservación textual. Preservar negaciones, relaciones, entidades y restricciones semánticas requiere juicios independientes; JSON válido no lo demuestra. |
| RW-03 | Mismo conjunto congelado, esquema, prompt, recuperador, índice, snapshot y qrels entre brazos. | Comparar original, Qwen y Gemma por consulta y por especificidad, conservando rankings y salidas. Medir Recall@5/20 y restricciones perdidas/alteradas. Una salida inválida se registra como fallo; cualquier fallback a original se declara y evalúa como una política distinta. |
| RW-04 | Revisiones y hashes de ambos modelos, entorno y parámetros fijados antes de generar. | Registrar tiempo hasta JSON validado, tokens, terminación y precisión. La configuración puede entrar en servicio después de medir fidelidad, calidad y latencia. |

Qwen2.5-7B-Instruct es el candidato principal. Gemma-3-1B-IT es el comparador generativo pendiente de fijar. EmbeddingGemma-300M conserva su función de encoder. La referencia de extracción clínica aportada motiva una hipótesis para Qwen; no demuestra una mejora de reescritura en este dominio.

La selección congelada tiene 300 consultas de prueba, 258 de desarrollo y 39 relacionadas reservadas. Los juicios son los positivos heredados de LitSearch, no nuevas anotaciones realizadas por el proyecto. El conjunto no acredita por sí solo las seis clases del futuro perfil de respuesta generativa ni las puertas QA-01 a QA-04.

[Decisión](../decisions/0006-bibliographic-pdf.md) · [Configuración del experimento](../../config/rewrite-experiment.json) · [Fuente del dataset](https://huggingface.co/datasets/princeton-nlp/LitSearch) · [Verificación](../verification/scientific-pdf.md)
