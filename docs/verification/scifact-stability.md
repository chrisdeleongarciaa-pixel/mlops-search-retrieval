# Verificación de estabilidad y encoder CUDA

| Requisito | Prueba o evidencia |
| --- | --- |
| Espacios separados y archivos oficiales | `config/embeddinggemma-300m.json`, verificación de Git blobs y SHA‑256 LFS y conformidad con `encode_query` / `encode_document`. |
| Mismas consultas y cohortes | Manifiesto de cada semilla, hashes de IDs elegibles y permutación, hashes de matrices y evidencia de índice vacío al inicio. |
| RET‑01 | `samples-*.jsonl.gz` conserva consulta, tiempo, trazas y profundidad. `aggregate.json` presenta media y desviación estándar por algoritmo y cohorte. |
| RET‑02 | `rankings.json` y `per-query.json` por construcción, con el evaluador BEIR fijado. La puntuación se calcula solo en la generación completa. |
| Runtime CUDA | `query_encoder_cuda.py` registra las formas capturadas, paridad de vectores y vecinos, tiempos y memoria por modo. |

El workflow **WP-01 and LX-01 witness** con `stability=true` ejecuta las cuatro construcciones de E5 usando el artefacto del ensayo original. Se despliega Vespa de nuevo antes de cada orden. El agregado acepta el conjunto completo de cuatro semillas de un modelo y declara si se completó la comparación de ambos.

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

[Contrato de comparación](../decisions/0005-encoder-comparison.md) · [Solicitud de acceso al checkpoint](https://huggingface.co/google/embeddinggemma-300m)
