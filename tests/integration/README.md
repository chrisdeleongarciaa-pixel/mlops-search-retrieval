# Integration tests

Use disposable services and synthetic originals. Record effective versions and check observable PostgreSQL, object-store and Vespa state. The [AV-04 witness](../../docs/verification/accept-revision.md#recovery-witness-av-04) kills the process after commit and before the receipt, then verifies replay.

```sh
python -m pip install -e ".[postgres,pdf]"
python -m unittest discover -s tests/integration -v
```

Set `ACCEPT_REVISION_TEST_DSN` to a disposable PostgreSQL database. The test role creates and drops each generated test schema. AV-04 and AV-13 require child process startup and termination. The suite reports a skip when the DSN is absent.

Set `WITNESS_VESPA_URL` to the deployed data endpoint for publication, retrieval and withdrawal. Follow the [PDF procedure](../../engineering/operations/scientific-pdf.md) for GROBID and [RI-01](../../docs/verification/integration-gate.md) for the real Qwen route. The automatic gate selects specific cases and checks their counts.
