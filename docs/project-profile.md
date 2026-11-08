# Recuperación de literatura científica con procedencia

## Portafolio

Desarrollé una plataforma experimental para buscar literatura de ML/NLP y preparar contexto verificable para un modelo generativo. El problema central es mantener la correspondencia entre los resultados, la revisión vigente del documento y la evidencia que recibe el modelo mientras el corpus cambia.

PostgreSQL registra la aceptación idempotente, los retiros y los eventos de publicación. Vespa sirve índices reconstruibles con BM25, búsqueda vectorial exacta y HNSW, con fusión RRF para la recuperación híbrida. En el laboratorio probé que un worker atrasado no vuelve a poner en búsqueda una revisión retirada. Para PDF científico, integré GROBID y una transformación de TEI que conserva secciones, coordenadas de página, rangos UTF-8 y hashes. Un ensayo con un original de ocho páginas produjo 94 fragmentos con localizadores comprobables. [Verificación de publicación](verification/witness-increments.md) · [Verificación de PDF](verification/scientific-pdf.md)

Construí el arnés de evaluación para distinguir calidad de ranking, aproximación y latencia. Sobre 300 consultas de SciFact, la recuperación exacta con EmbeddingGemma alcanzó **0,78871 NDCG@10**, frente a **0,66168** de BM25. La fusión de ambas bajó a **0,74246**, aunque elevó Recall@100 de **0,975** a **0,980** frente a EmbeddingGemma exacta. Esto identifica un intercambio entre cobertura y orden temprano que puede estudiarse con candidatos congelados. También preparé LitSearch con 64.183 artículos y una selección de 300 consultas para evaluación posterior. [Rankings y métricas de SciFact](../evaluation/evidence/scifact-gemma-exact-cpu/manifest.json) · [Contrato de LitSearch](contracts/bibliographic-profile.md)

**Tecnologías.** Python, PostgreSQL, Vespa, Docker Compose, GROBID, TEI, BEIR, PyTorch, Transformers, Sentence Transformers, vLLM y GitHub Actions. E5 y EmbeddingGemma-300M se evaluaron como encoders. Qwen2.5-7B-Instruct se ensayó como backend generativo con vLLM en una L4. [Registro experimental](registro-tecnico-experimental.md)

## CV

**Recuperación de literatura científica con procedencia** · Python, PostgreSQL, Vespa, GROBID, Docker, BEIR

- Implementé ingesta idempotente con outbox transaccional y publicación condicional en Vespa. Verifiqué continuidad y retiro ante trabajo atrasado con PostgreSQL y Vespa reales.
- Evalué BM25, búsqueda exacta, HNSW y RRF con rankings reproducibles. EmbeddingGemma exacta obtuvo 0,78871 NDCG@10 frente a 0,66168 de BM25 en 300 consultas SciFact.
- Integré transformación de PDF con GROBID, coordenadas de página y verificación de rangos y hashes. Materialicé LitSearch con 64.183 artículos y congelé 300 consultas de evaluación.

**English CV** · Scientific Literature Retrieval and Evidence Provenance

- Implemented idempotent ingestion with a transactional outbox and conditional publication. Verified query continuity and withdrawal under delayed workers using PostgreSQL and Vespa.
- Benchmarked BM25, exact vector search, HNSW and RRF. Exact EmbeddingGemma reached 0.78871 NDCG@10 against BM25 at 0.66168 on 300 SciFact queries.
- Integrated GROBID PDF extraction with page coordinates and byte-range verification. Prepared a 64,183-article LitSearch corpus and froze a 300-query evaluation set.

## LinkedIn

Construí un laboratorio de recuperación de literatura científica para estudiar qué ocurre cuando se actualiza o retira un artículo mientras hay búsquedas y trabajos de indexación en curso. Implementé aceptación idempotente en PostgreSQL, publicación condicional en Vespa y fragmentos de PDF que se pueden localizar por revisión, página y rango de bytes.

Un resultado que cambió el rumbo del proyecto fue el de SciFact. EmbeddingGemma con búsqueda exacta obtuvo 0,78871 NDCG@10 en 300 consultas. Al fusionarla con BM25 subió la cobertura a 100 candidatos, pero bajó NDCG@10. Conservar ambas listas por consulta permite revisar dónde se pierde un documento relevante y delimitar qué podría corregir un reranker. El laboratorio incluye además un piloto de inferencia con Qwen y vLLM, y un corpus LitSearch preparado para evaluar reescritura de consultas.
