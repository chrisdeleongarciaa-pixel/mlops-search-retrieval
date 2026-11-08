# Ejecución del incremento bibliográfico y PDF

El perfil de preparación usa CPython 3.12.10 en Windows amd64. `requirements-architecture-win64.lock` fija PyArrow, pypdf y defusedxml. `requirements-eval-win64.lock` fija el recálculo de BEIR. `requirements-pdf.lock` contiene las ruedas puras del parser cliente y se instala en la CI antes de descubrir contratos.

Desde la raíz del repositorio y con un entorno virtual nuevo, instalar con hashes y comprobar dependencias.

```powershell
python -m pip install --require-hashes -r config/requirements-architecture-win64.lock -r config/requirements-eval-win64.lock
python -m pip check
$env:PYTHONPATH = (Join-Path $PWD 'src')
python -B -m unittest discover -s tests/unit -v
python -B -O -m unittest discover -s tests/contract -v
python -B -m unittest discover -s evaluation -p test_litsearch.py -v
```

Los directorios de salida de los comandos siguientes deben ser nuevos. El materializador verifica las siete entradas contra el manifiesto fijado, reserva espacio, conserva título/resumen y comprueba todos los artículos citados por los juicios.

```powershell
python -B evaluation/prepare_litsearch.py <directorio-descargas> <salida-litsearch-300> --download
python -B evaluation/reproduce_scifact.py <scifact.zip> <beir-source.zip> evaluation/evidence/scifact-bm25-v1/run.json <salida-recalculo>
python -B evaluation/scifact_bm25.py <scifact.zip> <beir-source.zip> <salida-regeneracion>
```

El perfil de transformación requiere además el entorno del encoder y su snapshot verificado. El contrato de `receipt.json` es una exportación del operador desde la autoridad. Contiene `document` con tenant, fuente e ID, `sequence` y `original_sha256`. En el ensayo se obtiene tras aceptar el PDF en PostgreSQL y volver a leer el objeto usando una conexión independiente. El archivo recibido por la transformación es el original inmutable correspondiente.

```powershell
docker compose -p retrieval-pdf -f infra/grobid/compose.yaml up -d
curl.exe http://127.0.0.1:18070/api/version
python -B evaluation/pdf_transform.py <original> <receipt.json> <snapshot-e5> <salida-transformacion> --encoder e5 --generation scientific-pdf-v1
python -B evaluation/verify_transformation.py <salida-transformacion>
docker compose -p retrieval-pdf -f infra/grobid/compose.yaml stop
```

GROBID usa imagen por digest, concurrencia 1, carga diferida, JVM con heap máximo de 1 GiB, límite de contenedor de 2,5 GiB, memoria más swap limitada a 4 GiB y 2 CPU. El proceso pdfalto tiene límite configurado de 512 MiB y 60 s. El cliente permite hasta 20 MiB de original, 200 páginas, 8 MiB de TEI y 90 s por solicitud. La receta guarda hashes de configuración y Compose, junto con las opciones. La comprobación de versión HTTP se acompaña de la identidad de imagen/configuración observada por el operador. Un servicio externo con la misma versión requiere su propia atestación de despliegue.

La carga inicial bajo presión de memoria excedió el timeout y dejó un worker ocupado después de desconectar el cliente. El endpoint de versión no acredita que los modelos estén listos. El despliegue debe ejecutar un calentamiento de extracción controlado y comprobar su terminación antes de admitir trabajo. El límite del cliente se conserva: ampliar silenciosamente ese límite ocultaría el fallo del perfil de arranque. Todavía debe ensayarse un watchdog del worker y su recuperación.

La salida contiene `inputs.json`, TEI, texto canónico, manifiesto y resultado terminal. Ante un rechazo conserva inputs y resultado, con código tipado. Se usa un directorio temporal hermano y renombrado al terminar. Un corte anterior puede dejar staging, que debe tratarse como incompleto. El replay de una extracción usa el TEI conservado. Una extracción nueva puede incorporar metadatos diferentes de GROBID y recibe otra identidad al cambiar el hash del TEI.

La inspección visual del PDF y los juicios de extracción acompañan al verificador de bytes. Este último acredita integridad y cobertura del texto canónico. Las evaluaciones eSciBench/PLOS, Docling, límites de memoria y liberación de recursos tras timeout siguen sus ensayos propios. El cambio de generación con activación, retiro y rollback PDF se incorpora en el siguiente incremento de publicación.

[Contrato PDF](../contracts/scientific-pdf.md) · [Decisión bibliográfica](../decisions/0006-bibliographic-pdf.md) · [Evidencia](../verification/scientific-pdf.md)

La integración reproducible está en `tests/integration/test_scientific_pdf.py`. Requiere `ACCEPT_REVISION_TEST_DSN` apuntando a PostgreSQL desechable, `PDF_TEST_INPUT`, `PDF_TEST_SHA256` y `PDF_TEST_ENCODER_SNAPSHOT`. Opcionalmente, `PDF_TEST_TRANSFORM_PYTHON` identifica el Python del encoder, `PDF_TEST_GROBID` cambia el endpoint y `PDF_TEST_OUTPUT` conserva resultados en un directorio nuevo. Sin estas entradas se informa una omisión, no un pase. El test crea y elimina solamente su esquema generado.

```powershell
python -B -m unittest discover -s tests/integration -p test_scientific_pdf.py -v
```
