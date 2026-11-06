# Comparación de encoders y estabilidad de HNSW

**Estado.** Protocolo registrado. La promoción de un encoder y de un runtime depende de sus ensayos.

## Espacios y atribución

El control es `intfloat/multilingual-e5-base`. El candidato es `google/embeddinggemma-300m`, la primera generación de EmbeddingGemma. Cada modelo genera sus propios vectores y se publica en su propio índice. La identidad del espacio incluye revisión, archivos, prompt, límite, pooling, normalización, dimensión y tipo almacenado. Mezclar vectores de ambos espacios invalida la corrida. [Ficha oficial de EmbeddingGemma 300M](https://huggingface.co/google/embeddinggemma-300m)

La comparación principal conserva FP32, vectores L2 normalizados de 768 componentes en `float32`, coseno y un máximo común de 512 tokens efectivos. Ambos reciben el mismo título y abstract. E5 concatena los campos y usa sus prefijos. Gemma usa el título estructurado y los prompts de recuperación del checkpoint. Los IDs truncados se guardan por modelo. Cambios de dimensión, precisión, cuantización y longitud se consideran experimentos separados. Para Gemma, la ficha admite BF16 y FP32 en activaciones. [Ficha del modelo](https://huggingface.co/google/embeddinggemma-300m#usage)

La conformidad compara cada adaptador con la implementación publicada de su propio modelo. Exige coincidencia de IDs de tokens y máscaras, error máximo por componente de 1e-5 y coseno mínimo de 0,99999. En recuperación, el oráculo independiente de cada espacio usa coseno exhaustivo sobre los IDs elegibles y desempate por ID. La paridad exacta de Vespa se comprueba a 10 y 100.

## Comparación congelada

El protocolo fija las 300 consultas oficiales de SciFact BEIR test y sus qrels. Las 809 consultas de train sirven para comprobar el punto congelado. El punto primario conserva la exploración 0 que alcanzó el objetivo en el train original de E5, con `totalTargetHits=hits=100`. Se aplica a los dos modelos y a cada construcción. Las métricas de calidad se calculan en la generación completa con BM25, exacta, ANN y ambas fusiones RRF. Las cohortes con filtro comparan vecinos contra su propio oráculo exacto.

Cada orden de inserción usa un índice y volumen nuevos, una escritura en curso y los mismos 5.183 documentos. Las semillas registradas generan permutaciones de IDs mediante SHA‑256. Describen órdenes de inserción y no controlan el generador interno de Vespa. Cada semilla usa las 300 consultas en las cuatro cohortes de generación completa, 50 %, 10 % y 1 %. Esto da 1.200 observaciones por cohorte y 4.800 por modelo, con 300 consultas únicas. La composición de cada cohorte es la misma entre semillas y encoders.

El arnés mide primero sin trazado y después empareja trazas de la misma consulta y configuración. Reporta el algoritmo efectivo, la cobertura y la profundidad por separado. Calcula recall ANN@10, solapamiento@100 y p95 dentro de cada construcción, y luego su media y desviación estándar muestral entre construcciones. Cada p95 sujeto a RET‑01 necesita al menos 100 observaciones del algoritmo en cada construcción. El informe incluye diferencias emparejadas por orden cuando ambos modelos ejecutan el mismo algoritmo sobre la cohorte completa. El delta de latencia requiere además el mismo host del motor. Se registra si los entornos de codificación coinciden. Para una comparación controlada, materializa ambos modelos con el mismo perfil.

El ensayo anterior fijó `post-filter-threshold: 1.0`. El p95 de 38 ms al 10 % correspondió a HNSW con filtrado habilitado antes o durante la búsqueda. El 1 % se resolvió mediante búsqueda exacta por selectividad. El estudio nuevo usa un atributo numérico indexado para representar las mismas cohortes. Una atribución causal del coste del filtro requeriría repetir en el mismo grafo y con el mismo conjunto de IDs cambiando solo la representación del filtro. [Perfil de Vespa](https://docs.vespa.ai/en/reference/schemas/schemas.html#post-filter-threshold) · [Filtrado en Vespa](https://docs.vespa.ai/en/querying/nearest-neighbor-search-guide.html#controlling-filter-behavior)

## Ensayo de runtime

El primer ensayo conserva E5 y compara eager GPU, `torch.compile` y la misma compilación con reproducción explícita de CUDA Graphs. Después se aplica el mismo protocolo a Gemma. Los buckets fijos son 32, 64, 128, 256 y 512 tokens con lote de uno. Cada modo incluye tokenización y transferencias en la latencia de encoder. La preparación, compilación y captura se informan por separado junto con memoria asignada y reservada. Se exige paridad de vectores y top‑10 contra eager antes de considerar el runtime. La ruta completa con autoridad, búsqueda y revalidación conserva su propia puerta de 50 ms. El batching dinámico se evalúa por separado con cola, carga y límites declarados.

Las cifras de una A100‑80 GB y de EmbeddingGemma 2 no son resultados de este checkpoint ni del perfil L4. La memoria real se mide por runtime y precisión. Para un segundo backend, la ruta de vLLM usa el runner de pooling y debe pasar la misma paridad de prompts, vectores y vecinos. El ensayo de TensorRT requiere exportación y validación propias. [PyTorch CUDAGraph Trees](https://docs.pytorch.org/docs/stable/torch.compiler_cudagraph_trees.html) · [Modelos de pooling en vLLM](https://docs.vllm.ai/en/v0.16.0/models/supported_models/#embedding)

[Protocolo ejecutable](../../config/scifact-stability-v1.json) · [Ejecución y verificación](../verification/scifact-stability.md)
