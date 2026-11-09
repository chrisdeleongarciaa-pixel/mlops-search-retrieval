# Repository working instructions

- Start from the active contract. Use [durable acceptance](docs/contracts/accept-revision.md) for v1 ingestion and the [witness profile](docs/contracts/witness-increments.md) for v2 events, publication, retrieval and delivery. Check the [decision register](engineering/decisions/README.md) before extending guarantees to another profile.
- Use the [bibliographic profile](docs/contracts/bibliographic-profile.md) for LitSearch and query rewriting. For scientific PDFs, use the [transformation contract](docs/contracts/scientific-pdf.md) and [verification record](docs/verification/scientific-pdf.md). Activation requires the publication protocol.
- Record caller obligations, postconditions and observable invariants. Describe sequence guarantees as protocol properties. Validate external inputs, integrity and authorization explicitly.
- Maintain bidirectional traceability between requirements and tests. Derive expected results from requirements, independent models or calculated examples. Reproduce a defect with a failing test before correcting it.
- Separate instrumented tests, state properties, model exploration and real dependency tests. Record effective versions and outcomes. Distinguish contract verification from validation for intended use.
- After installing the package, run `python -m unittest discover -s tests/unit -v` and `python -m unittest discover -s tests/contract -v`. For integration changes, run the corresponding [integration cases](tests/integration/README.md) and record the outcome.
