# Recuperación de literatura científica e inferencia con citas

Plataforma experimental para buscar literatura de ML/NLP en inglés. PostgreSQL conserva la revisión autorizada y su estado de publicación. Vespa sirve proyecciones reconstruibles para búsqueda léxica y vectorial. Los fragmentos conservan la identidad de su revisión y su transformación para resolver la evidencia que recibe el modelo.

## Configuración registrada

El checkpoint `checkpoint/2026-11-08` reúne el laboratorio de ingesta, publicación y búsqueda, los ensayos de recuperación en SciFact y el incremento de PDF científico. El registro identifica los componentes verificados, las condiciones de ensayo y los criterios pendientes para ampliar su alcance. [Registro de configuración](docs/checkpoints/2026-11-08.md)

| Capacidad | Alcance demostrado |
| --- | --- |
| Aceptación y publicación | Ingesta idempotente con outbox transaccional. Continuidad de búsqueda y exclusión tras retiro bajo carreras ensayadas con PostgreSQL y Vespa. [Verificación](docs/verification/witness-increments.md) |
| Recuperación | BM25, búsqueda exacta, HNSW y RRF. En SciFact, EmbeddingGemma exacta alcanzó 0,78871 NDCG@10 frente a 0,66168 de BM25. [Resultados por consulta](evaluation/evidence/scifact-gemma-exact-cpu/) |
| Transformación PDF | GROBID, TEI y fragmentos con secciones, páginas, coordenadas y hashes. El ensayo de ocho páginas produjo 94 fragmentos verificables con presupuesto E5. [Verificación PDF](docs/verification/scientific-pdf.md) |
| Corpus bibliográfico | LitSearch materializado con 64.183 artículos y 300 consultas de evaluación congeladas. Los resultados de ranking y reescritura quedan pendientes. [Contrato bibliográfico](docs/contracts/bibliographic-profile.md) |
| Contexto e inferencia | Contratos de autorización, presupuesto de contexto y validación de citas en el testigo. Piloto aislado con Qwen2.5-7B-Instruct y vLLM en L4. [Verificación de entrega e inferencia](docs/verification/witness-increments.md) |

Sobre las 300 consultas oficiales de SciFact, E5 exacta obtuvo 0,69390 NDCG@10. RRF con EmbeddingGemma elevó Recall@100 de 0,975 a 0,980 y redujo NDCG@10 a 0,74246. En un ensayo distinto, HNSW con E5 y un cliente secuencial registró recall ANN@10 de 0,99158 y p95 de motor de 12,42 ms en la cohorte completa, como medias de cuatro construcciones. Las medidas de motor y de servicio tienen presupuestos distintos. La cohorte de ≈1 % utilizó búsqueda exacta por selectividad estimada. [Comparación exacta](evaluation/evidence/scifact-gemma-exact-cpu/manifest.json) · [Protocolo y resultados HNSW](docs/verification/scifact-stability.md)

La publicación de generaciones PDF, el rendimiento bajo carga representativa y la calidad de reescritura sobre LitSearch tienen criterios pendientes en el registro de configuración. [Estado frente al diseño](docs/estado-frente-al-diseno.md)

## Estructura

| Ruta | Responsabilidad |
| --- | --- |
| `src/search_retrieval/contracts/` | Identidades compartidas entre ingesta y consulta. |
| `src/search_retrieval/ingestion/` | Recepción de cambios, transformación, outbox e indexación. |
| `src/search_retrieval/query/` | Gateway, recuperación, ranking, contexto e inferencia. |
| `src/search_retrieval/adapters/` | Implementaciones de interfaces para servicios externos. |
| `config/` | Manifiesto de versión compatible y ubicación de configuración local. |
| `infra/` | Migraciones, aplicación Vespa, almacenamiento y serving. |
| `evaluation/` | Protocolo, datos sintéticos y manifiesto de ejecuciones independientes. |
| `tests/` | Pruebas unitarias, de contrato y de integración con PostgreSQL. |
| `docs/` | Límites de arquitectura, decisiones y procedimientos operativos. |

## Instalación y pruebas

Requiere Python 3.12 o posterior. Desde esta carpeta, ejecuta lo siguiente en PowerShell.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[pdf]"
.\.venv\Scripts\python.exe -m unittest discover -s tests/unit -v
.\.venv\Scripts\python.exe -m unittest discover -s tests/contract -v
```

En macOS o Linux, el ejecutable del entorno virtual es `.venv/bin/python`. La instalación puede requerir descargar el backend de empaquetado. Las pruebas unitarias se ejecutan sin motores externos ni GPU.

## Documentación de ingeniería

- Autoridad de estado, interfaces y evolución de versiones. [Descripción del sistema](docs/architecture.md) · [Decisiones](docs/decisions/README.md)
- Requisitos, cobertura y condiciones para aceptar cada capacidad. [Estado frente al diseño](docs/estado-frente-al-diseno.md) · [Objetivos de aceptación](docs/operations/service-objectives.md)
- Configuración y reproducción de ensayos. [Laboratorio de publicación y búsqueda](docs/operations/witness-lab.md) · [Procedimiento bibliográfico y PDF](docs/operations/scientific-pdf.md) · [Suite de integración](tests/integration/README.md)
- Resultados por experimento y línea de investigación. [Registro experimental](docs/registro-tecnico-experimental.md) · [Programa de recuperación aprendida](docs/programa-learned-retrieval.md)
- Presentación del proyecto. [Portafolio, CV y LinkedIn](docs/project-profile.md)
