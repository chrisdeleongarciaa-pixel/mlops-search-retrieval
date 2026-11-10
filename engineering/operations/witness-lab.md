# Reproducible witness

## Ingestion and publication

Use the observed Windows x64 profile with Python 3.12.10 and Linux amd64 containers. Set `RETRIEVAL_LAB_PASSWORD` outside source control and create a private originals directory.

```powershell
docker compose -f infra/postgres/compose.cpu.yaml up -d --wait
python -m pip install --require-hashes -r config/requirements-cpu-win64.lock -r config/requirements-pdf.lock
python -m pip install --no-deps --no-build-isolation -e .
```

Apply migrations in order, set `INGESTION_DSN`, `INGESTION_OBJECTS` and `WITNESS_VESPA_URL`, and wait for feed and search readiness.

```powershell
docker compose -f infra/vespa/compose.witness.yaml up -d
python -B infra/vespa/deploy.py http://127.0.0.1:19071
python -B -m search_retrieval.lab replay evaluation/fixtures/witness-feed.json evaluation/fixtures/witness.json
python -B -m search_retrieval.lab publish
python -B -m search_retrieval.lab search lab "¿Cuánto tiempo conserva LIMA el registro?"
```

Retain the database and originals when testing restart. Keep physical stale projections during a withdrawal test to exercise eligibility. Use a new generation if a fresh database shares an existing Vespa index. [Migrations](../../infra/postgres/migrations/README.md)

## Evaluation

Set `ACCEPT_REVISION_TEST_DSN` to a disposable database and `WITNESS_VESPA_URL` to the deployed data endpoint. For LX-01, use a separate published generation and pause writes during measurement.

```powershell
python -B -m unittest discover -s tests/integration -p test_witness_pipeline.py -k test_wp01 -v
python -B evaluation/vespa_lexical.py evaluation/fixtures/lexical-probes.json evaluation/runs/vespa-lx01
```

Create the parent output folder and use a new result directory. Save candidates, scores, valid empty outcomes and stage latencies. The `en-protocol` probe exercises the current body-only BM25 profile. The CPU gate runs WP-01 and LX-01 with pinned PostgreSQL and Vespa on Ubuntu. [CPU gate](../../docs/verification/integration-gate.md)

For SciFact install the evaluation lock with hash verification and obtain the pinned SciFact archive and BEIR archive at `ef83d29307061c65d04b035b4f4e7c18bd8374af`. Preserve manifests, rankings and per-query metrics. [Paired reproduction](../../docs/verification/scifact-stability.md)

For private delivery, bind `eligibility(tenant, principal)` to the authenticated invocation. Reuse that authority before context construction and final delivery. Public retrieval uses `eligibility(tenant)`. The target transport must provide authentication and keep private inference inside the authorized boundary. [Real model witness](../../docs/verification/integration-gate.md)
