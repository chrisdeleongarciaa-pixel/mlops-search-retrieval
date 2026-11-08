# Evidencia del incremento bibliográfico/PDF

`evidence-index.json` identifica los resultados observados y los hashes del código y la configuración local sobre la base `c1fd4fd`. Los JSON conservan los artefactos de ejecución. Para publicar `scifact-recalculation-inputs.json` se sustituyeron las rutas absolutas locales por nombres de archivo. El registro conserva el hash del manifiesto original y los hashes de las entradas. El corpus y los pesos permanecen fuera de Git.

`pdf-manifest.json`, `pdf-inputs.json` y `pdf-result.json` corresponden al intento final que pasó con PostgreSQL y GROBID reales. Los tres `failed-*.json` conservan los rechazos anteriores. El log de acceso distingue el timeout del cliente de la terminación posterior en el servidor. `pdf-container-effective.txt` contiene, en orden, memoria, memoria más swap, NanoCPUs e ID de imagen observados por Docker.

`pdf-independent-token-check.py` es una copia del verificador ejecutado desde `<workspace>/work/verify-pdf-token-budget.py`; conserva ese contexto de rutas. Su hash está en el resultado del recuento. El verificador reutilizable de rangos es `evaluation/verify_transformation.py`, y el recorrido completo se reproduce con `tests/integration/test_scientific_pdf.py` según la guía operativa.

La colección congelada de LitSearch identifica selección, juicios heredados y los hashes de sus artefactos. El recálculo SciFact y la regeneración BM25 se registran por separado. Ninguno de estos archivos contiene una ejecución de reescritura Qwen/Gemma ni métricas de recuperación sobre LitSearch.
