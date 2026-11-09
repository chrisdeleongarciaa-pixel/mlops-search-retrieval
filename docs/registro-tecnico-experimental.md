# 2 · Registro técnico y experimental

**Corte del 8 de noviembre de 2026.** Desde la aceptación de revisiones hasta la comparación exacta de EmbeddingGemma y E5. Las observaciones de cada corrida corresponden a su propio commit, entorno y población. Los ensayos a realizar después del corte se indican como pendientes.

## 1 · Corrección de la ingesta y la publicación

| Frontera | Precondición | Postcondición e invariante | Evidencia |
| --- | --- | --- | --- |
| Aceptación v1 | Ámbito autorizado, identidad y orden de origen válidos. Alta con original y digest, retiro sin original. | El recibo durable acredita el original verificado y el commit de revisión, estado deseado y outbox. Un reintento equivalente conserva el recibo y un conflicto conserva la aceptación previa. | AV-01 a AV-13. Cliente PostgreSQL independiente, lectura del original y oráculo de historias. |
| Evento v2 | Sobre versionado, ID de evento dentro de tenant y fuente. El alta identifica referencia, versión, tamaño y SHA-256 del original. | `submit_event` resuelve el ID conocido antes de aplicar el orden del documento. Compara documento, operación, revisión, posición y datos del original. Cursor y transporte quedan fuera de equivalencia. Otro ID en una posición aceptada se registra como obsoleto sin otra revisión ni outbox. | SE-01. Migración con historial v1, duplicado, conflicto y obsolescencia. |
| Replay | Manifiesto confiable de identidad estable y bytes verificables. | El checkpoint, separado de `source_position`, avanza por el prefijo contiguo de decisiones durables. Hueco, conflicto o resultado incierto detienen el avance. | SE-02. Cursor 1 con hueco y 4 tras reanudar el replay completo. |
| Persistencia | Lectura acotada del conector y verificación del objeto antes de registrar la aceptación. | El objeto puede quedar huérfano tras un corte previo al commit. Tras commit y antes de la respuesta, la revisión y el recibo ya existen. Reenviar resuelve la incertidumbre de transporte. | AV-04, AV-05, AV-12, AV-13 y SE-03. Inspección desde un cliente nuevo antes de reenviar. |
| Proyección y activación | Trabajo concedido por outbox, original verificable, generación y transformación fijadas. | La proyección por revisión y generación es inmutable. El manifiesto vincula todos sus fragmentos. PostgreSQL activa tras confirmar visibilidad y comprobar el token de concesión, la secuencia deseada, la generación y el retiro. | WP-01, WP-02 y TLC. Worker atrasado, actualización, retiro y alta posterior. |
| Búsqueda y contexto | Tenant autorizado, presupuesto, generación y espacio compatibles. | El motor filtra antes del límite. La autoridad revalida antes de devolver o reutilizar resultados. Si cambia el snapshot, hay como máximo un reintento. Vacío válido, falta de cobertura e incompatibilidad tienen salidas distintas. | WP-01, WP-02, RB-01 y regresiones densas e híbridas. |
| Entrega | Contexto autorizado, tokenizer y plantilla identificados, backend dentro del perímetro aplicable. | La respuesta privada se acumula y revalida antes de entrega. Las citas resuelven fragmentos presentes tras el presupuesto del prompt. En público, el texto provisional precede a un terminal explícito. | DL-01 a DL-07 con backend instrumentado. |

Las comprobaciones obligatorias en fronteras externas son explícitas. La ejecución con `python -O` comprueba que la validación sobrevive a la eliminación de aserciones. En v1 pasaron 4 pruebas unitarias, 8 de contrato y 12 de integración. Las historias concurrentes usaron conexiones reales, y 45 operaciones se contrastaron con el modelo independiente de aceptación. En v2 pasaron las regresiones v1 y SE-01 a SE-03. El modelo TLC 2.19 de publicación exploró 7.121 estados generados, 1.716 distintos y profundidad 18 sin violar su invariante. WP-02 usó PostgreSQL real e índice controlado. WP-01 usó PostgreSQL y Vespa reales. Los tres niveles observan propiedades distintas.

El parser testigo usa líneas UTF-8 con offsets de bytes semiabiertos, SHA-256 por fragmento, exclusión registrada de líneas vacías y límite de 1 MiB en el worker por defecto. La proyección vectorial añade el espacio a su identidad. Una actualización ordinaria conserva consultable la publicación anterior hasta activar la nueva. Un retiro corta la elegibilidad en la autoridad, incluso si el worker atrasado alcanza a escribir su proyección física. Los límites de 1.000 proyecciones autorizadas para el perfil mutable general y 400 fragmentos para el testigo vectorial acotan el filtrado por IDs. Son decisiones de laboratorio con coste de consulta y capacidad explícitos.

La ACL local usa concesiones directas por principal y documento, con clasificación privada a cargo del operador antes de activar la proyección. La ruta pública excluye los documentos privados. Las pruebas de entrega cubren revocación antes de contexto y durante generación, fallo de autoridad, cita eliminada por truncamiento, desconexión y liberación de una plaza de admisión, confirmación pública y saturación. En privado, la última lectura de autoridad fija la autorización bajo el contrato local de entrega acumulada. El transporte real todavía debe medir la ventana entre esa lectura y el primer byte y comprobar ACL-01 en todas las rutas.

[Contrato v1 y matriz AV](verification/accept-revision.md) · [Contratos SE, WP, RB y DL](contracts/witness-increments.md) · [Pruebas y TLC](verification/witness-increments.md) · [Worker y transformación](../src/search_retrieval/ingestion/publication.py) · [Contrato vectorial](contracts/dense-retrieval.md) · [Pruebas de entrega](../tests/contract/test_delivery.py)

## 2 · Entorno, aislamiento y cierre de WP-01

- **Entorno reproducible.** Python 3.12.10, Psycopg 3.3.8, PostgreSQL 17.11, Vespa 8.781.11 y `FileObjectStore` con persistencia local. Los locks fijan dependencias y hashes por plataforma. Las imágenes Linux amd64 se identifican por digest y se aplican las migraciones 0001 a 0004. Se verificaron `fsync`, `synchronous_commit` y `full_page_writes`. El corpus y la referencia contractual tienen identidad reproducible. [Perfil CPU](../config/cpu-win64.toml) · [Procedimiento](operations/witness-lab.md)
- **Diagnóstico local y Colab.** Docker/WSL podía publicar PostgreSQL en loopback, con unos 3,8 GiB visibles a Linux. Vespa rechazó el arranque por memoria. El intento con heap reducido avanzó hasta JDisc y agotó el plazo en ZooKeeper. Colab tenía RAM y disco suficientes para otros ensayos. Con Podman/crun se aisló el fallo de red del fallo de montaje. El namespace de usuario mapeado permitió levantar `lo` y transferir bytes. El montaje de `/proc` en un PID namespace nuevo siguió denegado. La L4 se aprovechó para codificación e inferencia en el runtime administrado. [Diagnóstico reproducible](verification/witness-increments.md)
- **GitHub Actions.** En Ubuntu 24.04 x64 se desplegó el perfil OCI. El primer feed chocó con el límite de disco de Vespa al 81,5 % y se añadió preparación de espacio y una sonda de escritura/búsqueda. En la segunda corrida pasó el test funcional y falló el paso de inspección porque faltaba `rg`. La corrección usó `grep`. En la tercera corrida pasó el job, con dos CPU, 7,75 GiB visibles y 63 % de disco ocupado tras descargar imágenes. WP-01 cubrió continuidad, acumulación de obsoletas por encima del top-k, retiro mientras trabajaba una actualización, contexto reutilizado y alta posterior. [Falló el feed](https://github.com/chrisdeleongarciaa-pixel/mlops-search-retrieval/actions/runs/42260751640) · [Pasó la prueba y falló la inspección](https://github.com/chrisdeleongarciaa-pixel/mlops-search-retrieval/actions/runs/42261463977) · [WP-01 y LX-01 aprobados](https://github.com/chrisdeleongarciaa-pixel/mlops-search-retrieval/actions/runs/42262268040)

## 3 · Referencia léxica y medición de servicio

LX-01 fijó una generación testigo y registró candidatos por solicitud para término, frase, acrónimo, identificador y consulta ausente. Completó 120 de 120 solicitudes con 40 vacíos válidos y cero errores observados. El identificador y la consulta ausente quedaron vacíos. En esa corrida, p95 de Vespa fue 22,64 ms y p95 total 64,43 ms. La descomposición posterior registra por ID de solicitud e intento la lectura inicial, motor, comprobación de cobertura cuando corresponde, revalidación y total. Cada percentil se calcula con muestras del tramo correspondiente. Restar los p95 del total y del motor no estima el p95 de la autoridad.

| Testigo posterior · corrida 42278239372 | p95 total | p95 encoder | p95 Vespa denso | p95 Vespa léxico |
| --- | ---: | ---: | ---: | ---: |
| BM25 | 42,10 ms | — | — | 14,04 ms |
| E5 densa | 147,02 ms | 104,84 ms | 14,41 ms | — |
| E5 híbrida | 180,86 ms | 133,99 ms | 14,02 ms | 6,84 ms |

Las tres rutas completaron 120 de 120 solicitudes cada una, con un cliente secuencial. El control vectorial usó E5 CPU precargado y cachés de consulta y resultados desactivadas. La codificación fue el tramo medido con mayor p95 en las rutas vectoriales, y las dos excedieron 50 ms de recuperación completa. Los p95 totales de ambas corridas léxicas describen ejecuciones distintas. Para evaluar una mejora de runtime se necesita una comparación pareada bajo el mismo entorno y carga.

[LX-01 original y muestras](../evaluation/evidence/wp01-lx01-actions-42262268040/manifest.json) · [Desglose y manifiestos posteriores](verification/dense-retrieval.md#testigo-con-comprobación-de-identidades-de-fragmento)

## 4 · Referencia exacta, conformidad y error de aproximación

**Protocolo.** SciFact de BEIR usa 5.183 documentos, 809 consultas de train y 300 de test, correspondientes estas últimas a dev del SciFact original. El protocolo fija corpus, qrels y evaluador BEIR con `pytrec_eval`. El train sirvió para elegir exploración frente al ranking exacto. Los qrels miden relevancia, mientras el ranking exhaustivo de cada espacio define sus vecinos. La fusión guarda las listas independientes y combina rangos mediante RRF con profundidad 100 por rama, constante 60, pesos iguales y desempate por ID. El top-10 de HNSW sale de la misma respuesta de 100 hits.

| Control | E5 | EmbeddingGemma-300M |
| --- | --- | --- |
| Checkpoint | `intfloat/multilingual-e5-base`, revisión `d128750597153bb5987e10b1c3493a34e5a4502a`. | `google/embeddinggemma-300m`, revisión `57c266a740f537b4dc058e1b0cda161fd15afa75`, con acceso autorizado a la revisión oficial. |
| Entrada | `query:` y `passage:`. Documento como título, un espacio U+0020 y abstract, con título vacío si falta. | Mismo título y abstract con título estructurado y prompts de recuperación del checkpoint. Conserva la modalidad requerida por Sentence Transformers al mover tensores de dispositivo. |
| Transformación | Truncamiento de la entrada efectiva a 512 tokens, promedio con máscara y normalización L2. | Límite común de 512 tokens efectivos, módulos de pooling y proyección del checkpoint y normalización L2. |
| Precisión y espacio | Inferencia FP32, vector `float32` de 768 dimensiones. | Igual precisión y dimensión, identidad de espacio propia. Cambiar encoder requiere recodificar y reconstruir índice. |
| Conformidad de adaptador | IDs y máscaras iguales en cuatro entradas. Pasaje largo 704 → 512, error absoluto máximo 0, coseno mínimo 0,99999988 en Windows CPU. También pasó en L4. | Referencia `encode_query` / `encode_document` con Sentence Transformers 6.2.0 en CPU. IDs y máscaras iguales. Pasaje largo 1.210 → 512, error máximo 9,50 × 10⁻⁸. |
| Datos guardados | Se observaron 731 documentos truncados y cero consultas de train truncadas. Matrices de corpus y train materializadas en L4 y también en el ensayo CPU. | Matrices verificadas de corpus, train y test. El bundle conserva por matriz IDs, hashes y registros de truncamiento. |

La tolerancia del adaptador es 10⁻⁵ por componente y coseno mínimo 0,99999. El fixture de vectores manuales verifica por separado coseno, orden y filtros, con recall ANN@2 de 0,5 y recall de relevancia@2 de 1,0 en su ejemplo. El oráculo SciFact calcula coseno exhaustivo en FP64 sobre vectores almacenados en `float32`, con desempate por ID. Vespa usa `prenormalized-angular` y puntúa únicamente con `1 - distance` en el perfil denso. `approximate:false` se compara con el oráculo sobre el conjunto elegible. La auditoría conserva coincidencia estricta y separa intercambios de corte dentro de 10⁻⁵ de cambios sustantivos de vecinos. En el piloto E5 las 360 comparaciones pasaron a 10 y 100, con coincidencia estricta de 1 y error máximo de score de 2,83 × 10⁻⁷. Las regresiones comprueban vacío elegible, vector faltante, incompatibilidad de generación, respuesta parcial y retiro durante trabajo atrasado.

[Perfil denso](contracts/dense-retrieval.md) · [Conformidad y auditoría E5](verification/dense-retrieval.md) · [Comparación de encoders](decisions/0005-encoder-comparison.md) · [Conformidad Gemma](../evaluation/evidence/embeddinggemma-300m-cpu/conformance.json) · [Ficha E5](https://huggingface.co/intfloat/multilingual-e5-base) · [Ficha EmbeddingGemma](https://huggingface.co/google/embeddinggemma-300m)

## 5 · HNSW, selectividad y estabilidad

El piloto E5 congeló exploración 0 al obtener 0,98900 de recall ANN@10 en train, con `totalTargetHits=100` y `hits=100`. En test con un cliente secuencial, generación completa obtuvo 0,98967 de recall y 8,78 ms p95 sobre 300 consultas. Alrededor del 10 %, 30 consultas dieron 1,00000 y 38,04 ms p95 con HNSW. El filtro estaba habilitado antes o durante la búsqueda, por lo que la cifra por sí sola no identifica coste de post-filtrado. Alrededor del 1 %, 30 consultas dieron recall 1,00000 y 6,48 ms p95 con exacta por selectividad, con 50 candidatos devueltos. Estos dos grupos de 30 sirvieron de diagnóstico inicial.

El estudio de estabilidad usó un atributo numérico indexado para los filtros y cuatro grafos nuevos de E5, con permutaciones de inserción reproducibles mediante SHA-256 y semillas 11, 29, 47 y 83. Cada construcción ejecutó las 300 consultas oficiales en cada cohorte. Las semillas controlan el orden de inserción, y las 1.200 observaciones por cohorte siguen correspondiendo a 300 consultas distintas. El total fue de 4.800 comparaciones por modelo en el protocolo completo. Se midió sin trazas y se emparejó después el plan con consulta, snapshot y configuración. El arnés distingue algoritmo, cobertura, objetivos de candidatos, hits solicitados y profundidad devuelta. La reproducción reclasificó el 1 % tras identificar el plan exacto junto a la decisión de filtro global `Skip` en la versión fijada.

| Cohorte | Algoritmo E5 | Recall ANN@10 | Solapamiento@100 | p95 de motor |
| --- | --- | ---: | ---: | ---: |
| Completa | HNSW | 0,99158 ± 0,00129 | 0,93716 ± 0,00180 | 12,42 ± 0,34 ms |
| ≈ 50 % | HNSW | 0,99658 ± 0,00110 | 0,96908 ± 0,00113 | 13,30 ± 3,24 ms |
| ≈ 10 % | HNSW | 0,99992 ± 0,00017 | 0,99925 ± 0,00021 | 10,30 ± 1,01 ms |
| ≈ 1 % | Exacta por selectividad | 1,00000 ± 0,00000 | 1,00000 ± 0,00000 | 6,44 ± 0,14 ms |

La tabla muestra media ± desviación estándar muestral de las métricas por construcción. Las tres cohortes HNSW pasaron RET-01 en las cuatro construcciones y en esa carga. El cambio de 38,04 a 10,30 ms en el 10 % coincide con cambios de implementación del filtro, cantidad de consultas y construcciones. Para atribuir el efecto al filtro, variar solo su representación con las mismas consultas, IDs elegibles y grafo.

**Estado del par con Gemma.** La corrida `42454352525` terminó en el paso de verificación del bundle, antes de construir los índices de la comparación. `sha256sum` informó que el archivo esperado no existía. El árbol del commit de entrada `21a17949d452114859458714332d68f83687cfb3` contiene el nombre `scifact-gemma-vectors.tar.gz` seguido de CR (`0x0D`).

El commit de entrada corregido `9dcdc5e23465d013b90bd1bb2420976fa7cbda12` conserva el mismo blob con la ruta exacta esperada. Se comprobaron los hashes de los ocho archivos internos y el SHA-256 `3c3af93cb430a5a8de79051a46bb6da200254848c033ce7ea2f39ffe2986ac2b` desde un checkout limpio. La corrida `42465181926`, con el workflow del mismo commit `c1fd4fd`, pasó la verificación y el desempaquetado en GitHub Actions. Al corte ya estaba construyendo y midiendo los grafos. El protocolo alterna los modelos entre semillas en el mismo runner.

[Piloto E5](verification/dense-retrieval.md#scifact-beir) · [Protocolo de estabilidad](../config/scifact-stability-v1.json) · [Agregado E5](../evaluation/evidence/scifact-stability-e5-42290872584/aggregate.json) · [Corrida fallida](https://github.com/chrisdeleongarciaa-pixel/mlops-search-retrieval/actions/runs/42454352525) · [Corrida corregida](https://github.com/chrisdeleongarciaa-pixel/mlops-search-retrieval/actions/runs/42465181926)

## 6 · Calidad observada y diagnóstico del modelo

| Ranking · 300 consultas oficiales | NDCG@10 | Recall@10 | Recall@100 |
| --- | ---: | ---: | ---: |
| BM25 | 0,66168 | 0,79094 | 0,88589 |
| E5 exacta | 0,69390 | 0,80722 | 0,93933 |
| E5 HNSW, piloto | 0,69163 | 0,80722 | 0,94600 |
| BM25 + E5 exacta | 0,70202 | 0,82722 | 0,95767 |
| BM25 + E5 HNSW, piloto | 0,70136 | 0,82722 | 0,95933 |
| Gemma exacta | 0,78871 | 0,91856 | 0,97500 |
| BM25 + Gemma exacta | 0,74246 | 0,85622 | 0,98000 |

Gemma exacta superó a E5 exacta por 0,09481 en NDCG@10 y 0,11134 en Recall@10. El conteo descriptivo de métricas guardadas por consulta arroja 91 mejoras, 175 empates y 34 retrocesos de NDCG@10. Por ejemplo, la consulta `691` pasó de 1,0 con E5 exacta a 0 con Gemma exacta y 0,5 con RRF de Gemma. En `1`, Gemma exacta obtuvo 1,0 y su RRF obtuvo 0. Estos casos localizan una pérdida en el ranking exacto o en la fusión, respectivamente, y dejan pendiente inspeccionar el texto, los truncamientos y los juicios para atribuirla semánticamente. En promedio RRF con Gemma gana 0,00500 de Recall@100 y pierde 0,04625 de NDCG@10 frente a su rama densa. Es una razón para preservar ambas listas y estudiar candidatos a reranking sin ajustar pesos sobre este test.

La evaluación exacta compara dos configuraciones completas de encoder y preprocesamiento con el mismo corpus, qrels y métrica. E5 CPU/Linux y Gemma CPU/Windows constan en el manifiesto. Los prompts y tokenizadores son propios de cada checkpoint, y esas condiciones limitan la atribución a una sola decisión de representación. El resultado de Gemma supera RET-02 como estimación puntual de exacta en SciFact. Quedan pendientes intervalos pareados, calidad con ANN y transferencia a otras colecciones.

**Secuencia de diagnóstico por consulta.** Comprobar primero corpus, qrels, elegibilidad y cobertura. Localizar el documento juzgado en BM25, exacto y HNSW y registrar su rango a 10 y 100. Si lo recupera exacto y lo pierde HNSW, inspeccionar plan, profundidad y diferencias de vecinos. Si lo pierde exacto, revisar título, tokens omitidos, entrada y negativos que lo superan. Si cae al fusionar, inspeccionar contribuciones de rangos y solapamiento. Si llega a la lista de 100 pero queda fuera del top-10, medir el techo de un reranker sobre esos candidatos. Registrar el caso como no juzgado cuando los qrels carezcan de decisión. Las hipótesis sobre cobertura, terminología, negación o razonamiento requieren auditoría de casos.

[Manifiesto exacto](../evaluation/evidence/scifact-gemma-exact-cpu/manifest.json) · [Métricas por consulta](../evaluation/evidence/scifact-gemma-exact-cpu/per-query.json) · [Rankings exactos y fusiones (gzip)](../evaluation/evidence/scifact-gemma-exact-cpu/rankings.json.gz) · [SciFact E5 y auditoría](verification/dense-retrieval.md#scifact-beir)

## 7 · Rendimiento de inferencia y verificación pendiente

El piloto generativo aislado en Colab L4 usó Qwen2.5-7B-Instruct BF16 con vLLM 0.33.0, contexto máximo 4.096 y NVIDIA 580.82.07. El primer arranque falló por Ninja en el calentamiento de FlashInfer. El muestreador nativo permitió arrancar, verificar `/health` y ejecutar 56 solicitudes. El mínimo libre fue 4.902 de 23.034 MiB, 21,28 %. El resumen está persistido y la descarga del ZIP crudo desde el notebook agotó su plazo. La cancelación activa y la integración con el gateway siguen pendientes.

| Concurrencia | Entrada + salida | Completadas | Tokens de salida/s | TTFT p95 | ITL p95 | E2E p95 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 1.024 + 96 | 16/16 | 16,91 | 276,00 ms | 57,37 ms | 5.692,73 ms |
| 4 | 1.024 + 96 | 16/16 | 57,00 | 1.097,81 ms | 59,65 ms | 6.823,64 ms |
| 8 | 1.024 + 96 | 16/16 | 97,07 | 2.168,63 ms | 60,97 ms | 8.399,80 ms |
| 8 | 3.800 + 256 | 8/8 | 81,63 | 8.215,01 ms | 66,44 ms | 25.066,45 ms |

El aumento de concurrencia en la carga corta elevó throughput agregado y TTFT. El ITL p95 se mantuvo entre 57,37 y 60,97 ms en los tres puntos cortos. E2E superó 3 s en los cuatro puntos aun con el backend aislado. Las colas y la carga representativa requieren sus propias mediciones para explicar el comportamiento bajo saturación. [Piloto L4 y configuración](verification/witness-increments.md#piloto-de-inferencia-bf16-en-la-l4-de-colab)

Para el encoder, el arnés implementado compara eager GPU, `torch.compile` y compilación con CUDA Graphs explícitos en buckets de 32, 64, 128, 256 y 512 tokens. Incluye tokenización y transferencias, separa calentamiento, compilación y captura, y exige paridad de vectores y vecinos frente a eager. Está pendiente la medición de consultas en GPU. El throughput de codificación de corpus en L4 y el piloto de Qwen responden a cargas distintas. El microbenchmark del encoder y el recorrido con PostgreSQL, Vespa, revalidación y cachés declaradas tienen presupuestos separados. [Arnés CUDA](../evaluation/query_encoder_cuda.py) · [Protocolo de runtime](decisions/0005-encoder-comparison.md#ensayo-de-runtime)

El protocolo de la siguiente entrega separa dispersión entre grafos, incertidumbre entre consultas y variación entre ejecuciones de servicio. Las evidencias de integración, los oráculos y las salidas por consulta permiten medir esas cantidades sin cambiar el test de SciFact. [Siguiente fase y protocolo estadístico](programa-learned-retrieval.md)

## 8 · Incremento bibliográfico y PDF del 8 de noviembre

Se adoptó el perfil ML/NLP en inglés con LitSearch y relevancia por artículo; español quedó fuera del alcance. Qwen2.5-7B es el candidato principal de reescritura y Gemma-3-1B el comparador generativo. EmbeddingGemma conserva el papel de encoder. La selección congelada contiene 300 consultas de prueba, 258 de desarrollo y 39 relacionadas reservadas, con siete archivos fuente verificados por hash. Los demás documentos siguen no juzgados. Esta colección tiene un contrato distinto del test de SciFact.

La preparación limpia permitió recalcular las tres métricas de BM25 y regenerar sus candidatos. Detectó dos dependencias transitivas faltantes en el lock de evaluación, corregidas antes de repetir. El resultado volvió a ser 0,66168 NDCG@10, 0,79094 Recall@10 y 0,88589 Recall@100. La corrida pareada 42465181926, antes descrita en curso, terminó con estado CI satisfactorio; su auditoría estadística permanece separada de este incremento arquitectónico.

El nuevo parser conserva TEI, texto UTF-8 canónico, secciones y cajas originales por página. La fragmentación usa el contador real del tokenizer y rechaza unidades indivisibles que excedan el presupuesto. Las identidades incluyen revisión, receta, generación y hash del TEI, de modo que cambios en localizadores no colisionan. Repetir la transformación del mismo TEI es determinista; volver a ejecutar un parser que agrega metadatos puede producir otra identidad.

La integración pasó con el PDF público de ocho páginas, PostgreSQL real y GROBID CRF: un original durable, una outbox, 94 fragmentos y 27.064 bytes canónicos, sin cajas ausentes. Un verificador independiente recorrió rangos y hashes. Se conservaron también el fallo inicial de formato de versión, el timeout de arranque y el 503 por pool ocupado. El servidor continuó trabajando después de desconectarse el cliente: la liberación del worker requiere una solución y ensayo propios. La presión de memoria motivó ajustar el límite del contenedor a 2,5 GiB.

Este incremento verificó integridad y el recorrido del caso observado. La validación de extracción sobre un corpus anotado, los juicios semánticos de reescritura, la carga objetivo y la integración de la generación PDF con publicación siguen abiertos. El repositorio conserva contratos, decisiones, manifiestos y trazabilidad; esto no acredita una auditoría completa de conformidad ISO/IEC/IEEE.

[Matriz de resultados y límites](verification/scientific-pdf.md) · [Contrato bibliográfico](contracts/bibliographic-profile.md) · [Contrato de transformación](contracts/scientific-pdf.md)
