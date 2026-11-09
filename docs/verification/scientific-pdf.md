# Verificación del incremento bibliográfico y PDF

Corte: 8 de noviembre de 2026. Esta entrega extiende delimitación, preparación de datos y transformación. La publicación de generaciones PDF y la comparación generativa de reescritura tienen puertas posteriores. Se conserva la distinción entre verificación contractual, integración real y validación de calidad.

| Requisito | Prueba / artefacto | Resultado observado |
| --- | --- | --- |
| BIB-01, BIB-02 | `evaluation/prepare_litsearch.py`, `evaluation/test_litsearch.py`, manifiesto de colección | Siete entradas verificadas por tamaño y SHA-256. 64.183 artículos; 300 consultas de prueba, 258 de desarrollo y 39 relacionadas reservadas. Tres pruebas del materializador/oráculo pasaron. |
| BIB-03 | `test_recall_oracle_and_missing_queries` | Caso calculado: Recall@5 = 0,5 y Recall@20 = 1. Cobertura incompleta y candidatos duplicados se rechazan. No se ejecutaron rankings sobre LitSearch en este incremento. |
| RW-01, RW-02 | `tests/contract/test_rewrite.py` | Dos pruebas pasaron: JSON inválido o incompleto, campos duplicados, `keep` alterado y pérdida de literal. |
| RW-03, RW-04 | `config/rewrite-experiment.json` | Protocolo especificado. Pendientes checkpoint del comparador, ejecución original/Qwen/Gemma y juicios semánticos. |
| Reproducción SciFact | `evaluation/reproduce_scifact.py`, `evaluation/scifact_bm25.py` | Entorno nuevo instalado con hashes y `pip check` correcto. Rankings congelados recalculados y ranking BM25 regenerado por separado. 300 consultas: NDCG@10 = 0,66168, Recall@10 = 0,79094 y Recall@100 = 0,88589. |
| PDF-01 | `test_pdf01_*` | Preflight de integridad, tamaño, páginas, cifrado y corrupción; separación de errores del servicio. |
| PDF-02, PDF-03 | `test_pdf02_*`, `test_pdf03_*` | TEI truncado/DTD rechazado; límites de salida/bloques/tokens; offsets UTF-8 calculados, múltiples páginas, ausencia explícita de localizador. |
| PDF-04 | `test_pdf04_*` | Reproducibilidad con TEI idéntico; nueva identidad al cambiar generación, receta o coordenadas. |
| PDF-01..05 real | `tests/integration/test_scientific_pdf.py` | Pasó con PostgreSQL 17.11 y GROBID 0.9.1 CRF. Original aceptado y leído desde conexión SQL independiente: 173.406 bytes, una outbox; ocho páginas, 94 fragmentos, 27.064 bytes canónicos y cero localizadores ausentes. |

La suite completa ejecutó **7 pruebas unitarias y 26 de contrato**, todas correctas. Los seis contratos PDF también pasaron con `python -O`. La integración real pasó en 143,990 s: la transformación reportó 129,157 s incluyendo preparación/verificación local, y el servidor registró 33,043 s para la extracción final con modelos cargados. Son observaciones individuales y no percentiles de servicio. La integración se omite explícitamente si faltan sus entradas y no cuenta una omisión como pase.

El ensayo PDF parte de un original científico de ocho páginas del repositorio de GROBID, fijado en el commit `523bb8e97f357e93682dc4eaa6d22faa8b5b93de`, con SHA-256 `3758229de3e08d024975e2c1b69bf89d960393def9f3827a515cebbc464c2aa2`. Se inspeccionó visualmente su primera página. Esa inspección cubre el caso observado; la exactitud de lectura y localizadores necesita una colección anotada más amplia.

El recuento independiente volvió a tokenizar los 94 fragmentos con el tokenizer E5 fijado, prefijo `passage: ` y tokens especiales: mínimo 6 y máximo 276, todos iguales a los conteos guardados y dentro de 512. El ambiente efectivo usó Transformers 5.20.2 y tokenizers 0.23.3. Esta corrida valida el presupuesto E5; la ruta PDF de EmbeddingGemma requiere su propia ejecución. También se verificó que desarrollo y prueba de LitSearch no comparten IDs ni artículos positivos.

Se encontraron y conservaron estos fallos:

- La reproducción limpia de BEIR requirió añadir `tqdm` y su dependencia Windows `colorama` al lock. Después pasaron el recálculo y la regeneración.
- El endpoint `/api/version` devuelve un objeto JSON. La primera implementación esperaba una cadena; una prueba reprodujo el fallo y pasó después de corregirlo.
- El primer intento de extracción agotó los 90 s del cliente. El servidor terminó esa solicitud con HTTP 200 después de 411,834 s. Otro intento recibió HTTP 503 por el pool ocupado. Ninguno emitió un manifiesto publicable.
- El límite inicial de 2 GiB registró presión de memoria (`memory.events max=6801`, sin OOM kill). Se ajustó a 2,5 GiB, con 4 GiB como límite de memoria más swap. La VM local expuso 3.049.902.080 bytes de RAM y cuatro CPU. Estos datos no son un benchmark de capacidad del parser.

La lectura de versión HTTP no basta como señal de readiness del trabajo. La cancelación del cliente tampoco garantiza el fin del worker. Quedan por ensayar el calentamiento controlado, watchdog, recuperación del pool, cortes de proceso durante staging, memoria pico y carga sostenida. eSciBench/PLOS y un corpus real de corruptos/límites aportarán la validación de extracción. El aislamiento de entidades XML, las pruebas sintéticas y un PDF científico no reemplazan esa validación.

[Contrato bibliográfico](../contracts/bibliographic-profile.md) · [Contrato PDF](../contracts/scientific-pdf.md) · [Comandos](../operations/scientific-pdf.md) · [Fuente del PDF](https://github.com/kermitt2/grobid/blob/523bb8e97f357e93682dc4eaa6d22faa8b5b93de/grobid-core/src/test/resources/sample1/sample.pdf)

[Índice de evidencia con hashes](../../evaluation/evidence/scientific-pdf-v1/evidence-index.json) · [Colección congelada](../../evaluation/evidence/scientific-pdf-v1/litsearch-collection.json) · [Recálculo BEIR](../../evaluation/evidence/scientific-pdf-v1/scifact-recalculation-result.json) · [Integración real](../../evaluation/evidence/scientific-pdf-v1/pdf-acceptance-verification.json) · [Manifiesto PDF](../../evaluation/evidence/scientific-pdf-v1/pdf-manifest.json)
