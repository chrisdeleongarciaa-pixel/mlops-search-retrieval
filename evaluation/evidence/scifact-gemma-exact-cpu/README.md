# SciFact · Comparación exacta de encoders

Resultados sobre las 300 consultas oficiales de prueba. `manifest.json` identifica métricas agregadas, espacios vectoriales y perfiles de codificación. `per-query.json` conserva las métricas individuales. `rankings.json.gz` contiene los rankings exactos, BM25 y las fusiones en JSON comprimido. `SHA256SUMS` identifica los tres artefactos archivados.

Los encoders se ejecutaron con los perfiles CPU registrados en el manifiesto. La comparación mantiene el corpus y las consultas. La influencia del motor ANN y la latencia de servicio se evalúan en ensayos separados. [Registro experimental](../../../docs/registro-tecnico-experimental.md)
