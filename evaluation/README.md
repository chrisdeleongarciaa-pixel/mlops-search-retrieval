# Evaluación

Esta área reúne experimentos reproducibles y oráculos independientes. Registra en [run.example.toml](run.example.toml) las referencias del corpus, consultas, juicios, versión evaluada, configuración del experimento y evaluador. Guarda las salidas locales en `evaluation/runs/`. Publica la evidencia reproducible en el almacén de artefactos aprobado para el proyecto.

Usa [fixtures sintéticos](fixtures/README.md) para calibrar el evaluador. En el baseline híbrido, conserva las listas de ambas ramas, su profundidad y el resultado fusionado.

El [run léxico de SciFact](evidence/scifact-bm25-v1/manifest.json) usa los qrels de BEIR y el evaluador del commit fijado. Se conservan el [ranking por consulta](evidence/scifact-bm25-v1/run.json) y las [métricas por consulta](evidence/scifact-bm25-v1/per-query.json). El repositorio de SciFact publica las anotaciones y los abstracts con licencias diferentes. [Licencias de SciFact](https://github.com/allenai/scifact/blob/master/LICENSE.md)
