# Verificación de estabilidad y encoder CUDA

| Requisito | Prueba o evidencia |
| --- | --- |
| Espacios separados y archivos oficiales | `config/embeddinggemma-300m.json`, verificación de Git blobs y SHA‑256 LFS y conformidad con `encode_query` / `encode_document`. |
| Mismas consultas y cohortes | Manifiesto de cada semilla, hashes de IDs elegibles y permutación, hashes de matrices y evidencia de índice vacío al inicio. |
| RET‑01 | `samples-*.jsonl.gz` conserva consulta, tiempo, trazas y profundidad. `aggregate.json` presenta media y desviación estándar por algoritmo y cohorte. |
| RET‑02 | `rankings.json` y `per-query.json` por construcción, con el evaluador BEIR fijado. La puntuación se calcula solo en la generación completa. |
| Runtime CUDA | `query_encoder_cuda.py` registra las formas capturadas, paridad de vectores y vecinos, tiempos y memoria por modo. |

El workflow **WP-01 and LX-01 witness** con `stability=true` ejecuta las cuatro construcciones de E5 usando el artefacto del ensayo original. Se despliega Vespa de nuevo antes de cada orden. El agregado acepta el conjunto completo de cuatro semillas de un modelo y declara si se completó la comparación de ambos.

Con `gemma_vectors_commit`, el mismo job toma un archivo `scifact-gemma-vectors.tar.gz` del commit indicado. El archivo contiene matrices y manifiestos de SciFact para train y test. El job alterna el orden de E5 y Gemma entre semillas y usa el mismo host para los ocho índices.

La revisión oficial de Gemma está sujeta a la licencia de Google. Con un snapshot local autorizado de la revisión fijada, instala el complemento de Sentence Transformers sobre el entorno denso y ejecuta estos comandos. Los directorios de salida deben ser nuevos.

```bash
python -m pip install --require-hashes --no-deps -r config/requirements-embeddinggemma.lock
python -m pip check
python evaluation/scifact_gemma.py "$gemma_snapshot" "$dataset" "$gemma_train" --device cuda
python evaluation/scifact_gemma.py "$gemma_snapshot" "$dataset" "$gemma_test" --split test --device cuda
```

Para cada encoder y semilla, despliega la misma versión de Vespa en un volumen nuevo y ejecuta el arnés. La ejecución valida la cobertura del corpus antes de abrir el split test.

```bash
python evaluation/scifact_stability.py run "$dataset" "$gemma_train" "$gemma_test" \
  "$beir_archive" "$output" --vespa "$vespa_url" --seed 11
python evaluation/scifact_stability.py aggregate "$report" "$e5_runs"/seed-*/manifest.json \
  "$gemma_runs"/seed-*/manifest.json
```

En un entorno CUDA compatible con TorchInductor, usa los pesos locales del encoder y su matriz de corpus del mismo espacio.

```bash
python evaluation/query_encoder_cuda.py e5 "$e5_snapshot" "$dataset" "$e5_bundle" "$e5_cuda"
python evaluation/query_encoder_cuda.py gemma "$gemma_snapshot" "$dataset" "$gemma_train" "$gemma_cuda"
```

Para medir el recorrido de servicio completo con E5 eager en GPU, ejecuta el testigo con PostgreSQL, Vespa y la ingesta preparada, y pasa `--device cuda` a `evaluation/vespa_vector.py`.

## Corrida E5 observada

La corrida `42290872584` reutilizó los vectores auditados y ejecutó 4.800 comparaciones en cuatro índices Vespa 8.781.11 independientes. Pasó la paridad exacta en todos los cortes. Los valores de la tabla son media ± desviación estándar muestral de cuatro métricas calculadas por construcción. Cada cohorte contiene 1.200 observaciones de 300 consultas únicas.

| Cohorte | Algoritmo efectivo | Recall ANN@10 | Solapamiento@100 | p95 del motor |
| --- | --- | ---: | ---: | ---: |
| Completa | HNSW | 0,99158 ± 0,00129 | 0,93716 ± 0,00180 | 12,42 ± 0,34 ms |
| ≈ 50 % | HNSW | 0,99658 ± 0,00110 | 0,96908 ± 0,00113 | 13,30 ± 3,24 ms |
| ≈ 10 % | HNSW | 0,99992 ± 0,00017 | 0,99925 ± 0,00021 | 10,30 ± 1,01 ms |
| ≈ 1 % | Exacta por selectividad estimada | 1,00000 ± 0,00000 | 1,00000 ± 0,00000 | 6,44 ± 0,14 ms |

Las tres cohortes HNSW cumplieron RET‑01 en las cuatro construcciones. El mayor NDCG@10 fue 0,70202, de RRF con exacta, por debajo del umbral 0,75. El agregado original marcó el 1 % como indeterminado. La reproducción verificó los hashes de las muestras y reconoció el plan exacto junto a la decisión `Skip` del filtro global. Vespa registra esa decisión cuando su estimación queda por debajo del límite inferior. La reclasificación conserva las latencias medidas y el agregado original. [Corrida E5](https://github.com/chrisdeleongarciaa-pixel/mlops-search-retrieval/actions/runs/42290872584) · [Decisión de filtro en Vespa 8.781.11](https://github.com/vespa-engine/vespa/blob/v8.781.11/searchcore/src/vespa/searchcore/proton/matching/query.cpp) · [Agregado de la reproducción](../../evaluation/evidence/scifact-stability-e5-42290872584/aggregate.json)

Pasaron 32 pruebas locales entre unitarias, de contrato y de evaluación. E5 repitió la conformidad FP32 con sus pesos fijados tras extraer el forward compartido, con error máximo por componente de 0.

La revisión oficial de EmbeddingGemma‑300M pasó la conformidad FP32 en CPU con Sentence Transformers 6.2.0. Coincidieron IDs de tokens y máscaras, el pasaje largo se truncó de 1.210 a 512 tokens y el error máximo por componente fue 9,50 × 10⁻⁸. La ejecución detectó que `preprocess` incluye la modalidad como texto. El adaptador conserva esa modalidad al mover tensores al dispositivo y el arnés CUDA exige que siga siendo igual a la del grafo capturado. Las mediciones CUDA requieren un entorno GPU. [Conformidad Gemma](../../evaluation/evidence/embeddinggemma-300m-cpu/conformance.json) · [Entorno](../../evaluation/evidence/embeddinggemma-300m-cpu/environment.json) · [Hashes del snapshot](../../evaluation/evidence/embeddinggemma-300m-cpu/snapshot-sha256.json)

[Contrato de comparación](../decisions/0005-encoder-comparison.md) · [Solicitud de acceso al checkpoint](https://huggingface.co/google/embeddinggemma-300m)
