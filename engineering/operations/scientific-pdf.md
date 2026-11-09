# Bibliographic and PDF procedures

The verified preparation environment uses CPython 3.12.10 on Windows amd64. The architecture lock pins PyArrow, pypdf and defusedxml. The evaluation lock pins BEIR dependencies. The parser client uses pure wheels for the automatic CPU gate.

```powershell
python -m pip install --require-hashes -r config/requirements-architecture-win64.lock -r config/requirements-eval-win64.lock
python -m pip check
$env:PYTHONPATH = (Join-Path $PWD 'src')
python -B -m unittest discover -s tests/unit -v
python -B -O -m unittest discover -s tests/contract -v
python -B -m unittest discover -s evaluation -p test_litsearch.py -v
```

Run from the repository root with a new output directory for each command. LitSearch verifies all seven source files, reserves disk space and validates positive article IDs.

```powershell
python -B evaluation/prepare_litsearch.py <download-directory> <litsearch-output> --download
python -B evaluation/reproduce_scifact.py <scifact.zip> <beir-source.zip> evaluation/evidence/scifact-bm25-v1/run.json <replay-output>
python -B evaluation/scifact_bm25.py <scifact.zip> <beir-source.zip> <regeneration-output>
```

Use a verified encoder snapshot and the Python environment for its tokenizer. Export the durable receipt from the authority with the document key, `sequence` and `original_sha256`. The original must be the immutable object referenced by that receipt.

```powershell
docker compose -p retrieval-pdf -f infra/grobid/compose.yaml up -d
curl.exe http://127.0.0.1:18070/api/version
python -B evaluation/pdf_transform.py <original> <receipt.json> <e5-snapshot> <transform-output> --encoder e5 --generation scientific-pdf-v1
python -B evaluation/verify_transformation.py <transform-output>
docker compose -p retrieval-pdf -f infra/grobid/compose.yaml stop
```

The pinned GROBID image uses one concurrent request, deferred loading, a 1 GiB JVM heap, 2.5 GiB container memory and 4 GiB memory plus swap. It uses two CPUs. pdfalto has limits of 512 MiB and 60 s. The client allows 20 MiB originals, 200 pages, 8 MiB TEI and 90 s per request. The recipe records Compose and configuration hashes. Operator attestation binds the responding service to those inputs.

Run a controlled warm extraction before admitting work. The version endpoint establishes version only. The cold-run timeout left the worker occupied and requires recovery verification. Outputs retain inputs, TEI, canonical text, manifest and terminal result. A rejection retains inputs and status. A process cut can leave an incomplete staging directory. Replay the retained TEI for deterministic transformation.

For the real integration, export `ACCEPT_REVISION_TEST_DSN` for a disposable database, `PDF_TEST_INPUT`, `PDF_TEST_SHA256` and `PDF_TEST_ENCODER_SNAPSHOT`. Optional variables are `PDF_TEST_TRANSFORM_PYTHON`, `PDF_TEST_GROBID` and `PDF_TEST_OUTPUT`. The test isolates its generated schema and reports an explicit skip when inputs are missing.

```sh
python -B -m unittest discover -s tests/integration -p test_scientific_pdf.py -v
```

[PDF contract](../../docs/contracts/scientific-pdf.md) · [Observed evidence](../../docs/verification/scientific-pdf.md)
