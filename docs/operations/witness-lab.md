# Laboratorio reproducible

## Ingesta y publicación

Usa Python 3.12.10 en Windows x64 y Docker con contenedores Linux amd64. Los archivos de lock fijan las ruedas del perfil observado. El [compose CPU](../../infra/postgres/compose.cpu.yaml) arranca PostgreSQL con almacenamiento persistente y persistencia local de WAL. Configura `RETRIEVAL_LAB_PASSWORD` fuera del repositorio y prepara un directorio privado para los originales.

```powershell
docker compose -f infra/postgres/compose.cpu.yaml up -d --wait
python -m pip install --require-hashes -r config/requirements-cpu-win64.lock
python -m pip install --no-deps --no-build-isolation -e .
```

Aplica las [migraciones en orden](../../infra/postgres/migrations/README.md). Configura `INGESTION_DSN` y `INGESTION_OBJECTS` con la conexión y el directorio existente. Arranca Vespa en loopback con el digest del [manifiesto CPU](../../config/cpu-win64.toml). Aplica el paquete y espera a que el endpoint de datos esté listo antes de consumir la outbox. [API de despliegue Vespa](https://docs.vespa.ai/en/reference/api/deploy-v2.html)

```powershell
docker compose -f infra/vespa/compose.witness.yaml up -d
python -B infra/vespa/deploy.py http://127.0.0.1:19071
python -B -m search_retrieval.lab replay evaluation/fixtures/witness-feed.json evaluation/fixtures/witness.json
python -B -m search_retrieval.lab publish
python -B -m search_retrieval.lab search lab "¿Cuánto tiempo conserva LIMA el registro?"
```

`WITNESS_VESPA_URL` indica el endpoint de datos. Reutiliza la base y el directorio de originales al reiniciar para conservar recibos y el checkpoint. Conserva las proyecciones durante el ensayo de retiro, para comprobar la exclusión incluso con datos físicos presentes. Para empezar con una base nueva sobre un índice existente, configura una generación nueva antes de publicar. El laboratorio emplea una generación activa y un conjunto autorizado acotado.

## Evaluación

### WP-01 y referencia léxica LX-01

En GitHub Actions, ejecuta manualmente [WP-01 and LX-01 witness](../../.github/workflows/wp01.yml) en un runner Ubuntu 24.04 x64. El workflow usa las imágenes fijadas, verifica escritura y lectura con cobertura completa y ejecuta WP-01. Si pasa, publica el corpus sintético en una base separada y mide LX-01. El artefacto `wp01-witness` conserva los logs del ensayo y los resultados de las consultas.

Tras desplegar el paquete de Vespa, exporta `ACCEPT_REVISION_TEST_DSN` con una base desechable y `WITNESS_VESPA_URL` con el endpoint de datos. Ejecuta WP-01 antes de medir. Para LX-01 usa una base separada con el feed testigo publicado y pausa escrituras durante toda la medición. Conserva el manifiesto de despliegue efectivo junto con el resultado.

```powershell
python -B -m unittest discover -s tests/integration -p test_witness_pipeline.py -k test_wp01 -v
python -B evaluation/vespa_lexical.py evaluation/fixtures/lexical-probes.json evaluation/runs/vespa-lx01
```

Crea el directorio padre de la salida y elige un directorio de ejecución nuevo. LX-01 guarda candidatos, scores y tiempos al motor y del recorrido de recuperación. Distingue vacíos válidos y errores. El identificador `en-protocol` es un sondeo porque el esquema actual aplica BM25 sobre `body`. Los resultados con un cliente sirven como referencia del testigo. El [perfil de aceptación](service-objectives.md) define por separado el ensayo de carga.

### Evaluación offline

Ejecuta el oráculo sintético con Python estándar. El [runner de SciFact](../../evaluation/scifact_bm25.py) verifica ambos ZIP y el código del evaluador antes de cargarlo. En un entorno aislado, instala `config/requirements-eval-win64.lock` con `--require-hashes`. Obtén el ZIP de SciFact y el ZIP de código de BEIR del commit `ef83d29307061c65d04b035b4f4e7c18bd8374af`. Ejecuta `python -B evaluation/scifact_bm25.py <scifact.zip> <beir-source.zip> <directorio-nuevo>`. Conserva manifiesto, resultados ordenados y métricas por consulta. La [referencia](../contracts/reference/v1/CONTRATOS_Y_ENTORNO_REPRODUCIBLE_RETRIEVAL.md) fija el resto de evaluadores. [Origen del dataset BEIR](https://github.com/beir-cellar/beir/wiki/Datasets-available/2b0814595afb2b4ed1aa5fc76c04ca5e1eb142e9)

## Inferencia y privacidad

El adaptador vLLM utiliza el endpoint compatible de chat y el contador usa el chat template del tokenizer local. El [manifiesto de fuentes de modelos](../../config/model-sources.json) conserva las revisiones y los checksums esperados de E5 y Qwen. En el destino A100 registra primero el driver, CUDA, VRAM, RAM y disco. Materializa la imagen y los archivos del modelo por digest antes de lanzar Qwen2.5-7B-Instruct en BF16 con 4.096 tokens de contexto y 256 de salida. Mide por separado concurrencias 1, 4 y 8, latencia al primer token, latencia total, rendimiento, errores y liberación de recursos al desconectar.

Usa la búsqueda pública con `eligibility(tenant)` y la privada con una función que consulte `eligibility(tenant, principal)` para la identidad autenticada de esa invocación. La misma función de autoridad debe proteger la construcción del prompt y la entrega final. Sirve los originales privados únicamente dentro del perímetro autorizado. El cuerpo privado sale acumulado y validado. La API de laboratorio deja al integrador la autenticación, el transporte público y la integración con el proveedor de identidad.
