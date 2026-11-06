# Verificación de recuperación después de LX-01

| Requisito | Evidencia |
| --- | --- |
| Conformidad FP32 de E5 | `embeddings/conformance.json` comprueba los IDs y máscaras observados durante inferencia. Compara contra tokenización y pooling del ejemplo oficial e informa error por componente y coseno por entrada. `environment.json` fija el dispositivo, versiones y hashes. |
| Coseno y filtros | `tests/unit/test_ranking.py` usa vectores manuales. `scifact/samples.json` compara el oráculo matricial y `approximate:false` por ID, score y corte. |
| RET-01, piloto | `scifact/freeze.json` guarda la elección en 809 consultas de train. El manifiesto de test informa recall ANN@10, solapamiento@100, profundidad y p95 con 100 hits solicitados. Los planes se agrupan por cohorte y algoritmo. |
| RET-02 | `scifact/rankings.json` conserva BM25, exacta, ANN y ambas fusiones. `per-query.json` y el manifiesto registran la comparación en las 300 consultas de test de BEIR, correspondientes a dev del SciFact original. |
| SRV-01, componente de recuperación | `lx01/samples.json` y `vector-service/*-samples.json` emparejan tramos por solicitud e intento. El control vectorial usa E5 cargado y cachés de consultas y resultados desactivadas. El piloto de un cliente mide el componente de recuperación del perfil. |
| Retiro y cobertura | `tests/contract/test_vector_search.py` comprueba los errores y el reintento. Las dos historias vectoriales de `test_witness_pipeline.py` usan PostgreSQL y Vespa reales con un encoder controlado. |

Ejecutar el workflow **WP-01 and LX-01 witness** con la opción `dense`. La corrida instala los locks Linux, aplica migraciones, despliega la aplicación Vespa fijada y exporta la evidencia en el artefacto `wp01-witness`. El test léxico conserva su carga original. El test SciFact congela la exploración antes de leer los qrels de test.

La latencia de cada tramo se agrega desde sus observaciones directas. La corrida de SciFact mide el motor con un cliente y vectores ya calculados. La corrida mutable incorpora codificación, cobertura y ambas lecturas de elegibilidad. Las métricas de calidad se calculan con BEIR fijado y `pytrec_eval` sobre listas independientes. Si una configuración congelada incumple el umbral, se conserva el resultado.

## Resultados observados

- La conformidad local de E5 en Windows y CPU pasó en FP32 con PyTorch 2.14.0+cpu y Transformers 5.20.2. Coincidieron los IDs y las máscaras en las cuatro entradas. El pasaje largo pasó de 704 a 512 tokens. El error absoluto máximo fue 0 y el coseno mínimo observado fue 0,99999988. [Resultados](../../evaluation/evidence/e5-conformance-win64/conformance.json) · [Entorno](../../evaluation/evidence/e5-conformance-win64/environment.json)
- Pasaron 7 pruebas unitarias y 18 de contrato, incluidas las de cobertura, generación y reintento vectorial.

[Contrato denso e híbrido](../contracts/dense-retrieval.md) · [Ficha oficial de E5](https://huggingface.co/intfloat/multilingual-e5-base#usage) · [HNSW y filtros en Vespa](https://docs.vespa.ai/en/querying/approximate-nn-hnsw.html)
