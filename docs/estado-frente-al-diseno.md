# 1 · Estado del sistema frente al diseño

**Corte del 8 de noviembre de 2026.** Configuración `checkpoint/2026-11-08` en la rama `codex/dense-retrieval`. El avance experimental cubre recuperación densa e híbrida en SciFact. El incremento arquitectónico incorpora el dominio en inglés, LitSearch y transformación de PDF con procedencia. El cierre experimental de Gemma requiere revisar los artefactos del ensayo pareado y cuantificar incertidumbre. [Registro de configuración](checkpoints/2026-11-08.md)

La numeración sigue la secuencia acordada en la conversación. El diseño técnico v0.1, identificado por SHA-256 `f20341b89c841b5a72ec05c987a26d34d96ed73344332fe754d56d794fe5b18e`, aporta las responsabilidades y requisitos candidatos. La referencia versionada define el entorno y los protocolos de evaluación. El perfil de aceptación v1 aporta los umbrales adoptados después del diseño. [Arquitectura del repositorio](architecture.md) · [Contratos y entorno](contracts/reference/v1/CONTRATOS_Y_ENTORNO_REPRODUCIBLE_RETRIEVAL.md) · [Objetivos de aceptación v1](operations/service-objectives.md)

## Checklist por incremento

Una casilla marcada acredita el alcance de la columna **Hecho y evidencia**. El cierre general de cada capacidad se decide contra la columna **Falta** y el perfil de destino.

| Paso | Hecho y evidencia | Falta para el alcance del diseño |
| --- | --- | --- |
| 0 · Delimitación | [x] Perfil bibliográfico ML/NLP en inglés, LitSearch-300 con juicios heredados por artículo, SciFact como regresión y testigo para contratos. Español fuera del alcance. Qwen es candidato principal de reescritura. Carga y retención del laboratorio especificadas. | [ ] Medir capacidad, fidelidad de reescritura y ejecutar las reglas de retención. La A100 requiere justificación de carga. La GPU observada en Colab fue una L4. |
| 1 · Contratos y entorno | [x] Contratos v1/v2, publicación, consulta y entrega. Identidades y estados diferenciados. Copia versionada con SHA-256, locks CPU por plataforma, migraciones y oráculo testigo independiente. | [ ] Materializar locks de los demás evaluadores, manifiestos de datasets, perfiles de despliegue y procedencia de cada run. Cerrar las decisiones de arquitectura con evidencia del perfil al que se aplican. |
| 2 · Ingesta durable | [x] `accept_revision` y `submit_event`, originales verificados, recibo e identidad persistidos con revisión y outbox, checkpoint contiguo. Migración con historial v1 y cortes antes y después del commit verificados. | [ ] Replay con conector real, carga y backlog representativos, reconciliación de huérfanos y operación de restauración. |
| 3 · Parsing y fragmentación | [x] Proyección testigo y unidad documental SciFact reproducibles. Nuevo adaptador GROBID y transformación TEI con offsets UTF-8, hashes, secciones, cajas por página y presupuesto real del tokenizer. Contratos y ensayo real registrados en la matriz PDF. | [ ] Validación de extracción sobre corpus anotado, fallos reales por formato, arranque y liberación de recursos bajo timeout. Integrar la generación PDF con publicación, reprocesamiento y rollback. |
| 4 · Publicación, búsqueda y retiro | [x] WP-01 pasó con PostgreSQL y Vespa reales. Verificó continuidad, retiro con trabajo atrasado, revisiones obsoletas fuera del top-k y contexto reutilizado. WP-02 y TLC añadieron evidencia separada para ACL y el modelo de publicación. | [ ] Validar ACL-01 < 1 s en todas las rutas reales. Ensayar reconciliación, limpieza, reconstrucción y cambios de generación a escala. |
| 5 · Léxica | [x] LX-01 conserva candidatos, vacíos válidos y latencias del testigo. Baseline BM25 documental de SciFact con 300 consultas y rankings guardados. | [ ] Cobertura de casos técnicos en el corpus objetivo. El identificador de LX-01 produjo vacío. Para exigir aciertos, congelar ejemplos y evaluar el campo exacto y el analizador. |
| 6 · Densa exacta y ANN | [x] Conformidad de ambos encoders con su referencia. E5 verificó paridad exacta en Vespa y HNSW en cuatro construcciones. Gemma exacta obtuvo NDCG@10 de 0,78871. | [ ] Completar el ensayo pareado de Gemma en Vespa y los intervalos de incertidumbre. Medir GPU y latencia completa en el perfil objetivo. |
| 7 · Híbrida | [x] RRF de BM25 con E5 exacta y HNSW. RRF exacta con Gemma. Rutas mutables densa e híbrida verificadas ante retiro en el testigo. | [ ] Medir Gemma HNSW + BM25, repetir cobertura y latencia con el candidato y atribuir los cambios de RRF en un conjunto de desarrollo. |
| 8 · Reranking | [ ] Candidato por implementar y medir. | [ ] Primero evaluar un cross-encoder sobre candidatos congelados. Después medir la cascada con profundidad, presupuesto y coste declarados. Reservar otras familias para fallos que se puedan aislar. |
| 9 · Contexto y procedencia | [x] Perfil testigo con identidades de fragmento, revalidación, presupuesto de prompt y control de citas contra el contexto efectivo. | [ ] Diagnosticar cobertura de evidencia por salto, deduplicación, orden y truncamiento sobre datos representativos. Evaluar soporte de afirmaciones con juicios independientes. |
| 10 · Backend de inferencia | [x] Piloto aislado de Qwen2.5-7B-Instruct BF16 con vLLM en Colab L4. | [ ] Caracterización en A100 cuando haya carga que la justifique. Validar tiempos, memoria y cancelación activa en el backend de destino. |
| 11 · Gateway y recorrido integral | [x] Operaciones de entrega pública y privada, y adaptador vLLM en código. Pruebas de contrato con backend instrumentado. | [ ] Integrar identidad autenticada, deadlines, búsqueda, contexto e inferencia en un transporte real. Verificar cancelación, streaming y autorización hasta la emisión. |
| 12 · Validación y operación | [x] Fallos acotados de ingesta, publicación y entrega reproducidos. | [ ] Carga representativa, seis clases de consulta, restauración, fallos de dependencias, observabilidad y validación de QA-01 a QA-04, SRV-01, SRV-02 y ACL-01. |
| 13 · Promoción, despliegue y mantenimiento | [x] CI del testigo, locks, manifiesto de versión de ejemplo y evidencia por corrida. | [ ] Promoción y rollback de un conjunto compatible de código, datos, índice, modelos y políticas. Despliegue objetivo, monitoreo y procedimientos de reconstrucción y retiro. Kubernetes/GitOps queda condicionado al despliegue adoptado. |

[Verificación de ingesta](verification/accept-revision.md) · [Testigo y entrega](verification/witness-increments.md) · [Recuperación densa](verification/dense-retrieval.md) · [Estabilidad HNSW](verification/scifact-stability.md) · [Registro técnico y experimental](registro-tecnico-experimental.md)

## Puertas de aceptación

| Puerta | Lectura al corte |
| --- | --- |
| **RET-01** · Recall ANN@10 ≥ 0,95 y p95 de motor < 25 ms | E5 cumplió ambos en las tres cohortes HNSW de las cuatro construcciones, con un cliente secuencial. El 1 % activó exacta por selectividad y se informa aparte. Pendientes Gemma, capacidad y carga objetivo. |
| **RET-02** · NDCG@10 de SciFact ≥ 0,75 | E5 quedó bajo el umbral. Gemma exacta lo superó por 0,03871 como resultado puntual. Gemma + BM25 RRF exacta quedó en 0,74246. Gemma ANN sigue sin resultado. |
| **SRV-01** · Recuperación < 50 ms p95 y objetivos de carga e inferencia | El testigo más reciente pasó en léxica con 42,10 ms. Densa E5 CPU e híbrida midieron 147,02 y 180,86 ms. En el piloto aislado L4, el E2E superó 3 s en los cuatro puntos. Falta medir el servicio integral con el perfil de 8 y 16 usuarios. |
| **ACL-01** · Revocación < 1 s | Fencing, revalidación y exclusión tienen evidencia funcional en perfiles acotados. La cota temporal y la carrera entre la última lectura de autoridad y la emisión siguen abiertas. |
| **SRV-02 y QA-01 a QA-04** | Pendientes el objetivo de disponibilidad de 99,9 % en 30 días, la recuperación < 120 s por fallo y la evaluación de fidelidad, relevancia, contexto y afirmaciones sin respaldo. |

[Objetivos y definiciones](operations/service-objectives.md) · [Resultado exacto Gemma](../evaluation/evidence/scifact-gemma-exact-cpu/manifest.json) · [Agregado E5](../evaluation/evidence/scifact-stability-e5-42290872584/aggregate.json)

## Orden del siguiente trabajo

1. Completar las puertas de la [transformación PDF](verification/scientific-pdf.md) y conectar su generación candidata con publicación, búsqueda y retiro. La corrida pareada corregida terminó con estado CI satisfactorio; quedan por auditar sus métricas y artefactos antes de cerrar paridad, recall, profundidad, latencia e incertidumbre. Medir por separado el encoder y la recuperación completa. [Corrida pareada corregida](https://github.com/chrisdeleongarciaa-pixel/mlops-search-retrieval/actions/runs/42465181926)
2. Usar la referencia congelada para evaluar transferencia en otras colecciones BEIR y en BRIGHT. Conservar las listas independientes y los resultados negativos. Integrar el reranker en dos etapas una vez delimitada la cobertura de candidatos.
3. Continuar contexto, autorización, gateway, operación y promoción por sus contratos. Usar la misma plataforma como banco de pruebas para experimentos de representación con versiones aisladas y puertas propias. [Programa de investigación](programa-learned-retrieval.md)
